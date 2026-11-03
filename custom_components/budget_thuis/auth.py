"""Passwordless (magic-link) login for Budget Thuis.

Budget Thuis replaced username/password with an e-mailed login link. The OAuth2
Authorization Code + PKCE flow around it is unchanged, so this module only
replaces the "submit credentials" step:

1. ``async_request_link``: start ``/connect/authorize`` (PKCE), load the
   ``/inloglink-aanvragen`` page and POST the e-mail address. Budget Thuis then
   mails a one-time login link.
2. ``async_complete``: the user pastes that link (instead of clicking it). We
   open it in the *same* cookie session, follow the redirects until the
   ``budgetthuis://login_success?code=...`` app redirect, and exchange the code
   (with our PKCE verifier) for tokens.

Only the resulting refresh token is stored; the rest of the integration keeps
using ``aiobudgetthuis`` for token refresh and data calls.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import logging
import re
import secrets
import time
from typing import Final, override
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

from aiobudgetthuis import BudgetThuisAuthError, BudgetThuisConnectionError, Tokens
import aiohttp

_LOGGER = logging.getLogger(__name__)

ACCOUNTS_URL: Final = "https://accounts.budgetthuis.nl"
CLIENT_ID: Final = "mobile"
REDIRECT_URI: Final = "budgetthuis://login_success"
SCOPE: Final = "mobileApi webApi offline_access openid email idsServiceExternal"
LABEL: Final = "BudgetEnergie"
USER_AGENT: Final = (
    "Budget Thuis/3.1.0 (nl.budgetthuis.app; build:2940; iOS 26.6.0) Alamofire/5.10.2"
)
REQUEST_LINK_PATH: Final = "/inloglink-aanvragen"

_REDIRECTS: Final = (301, 302, 303, 307, 308)
_MAX_HOPS: Final = 15
_HTTP_OK: Final = 200
_MAX_FORMS: Final = 3
_META_REFRESH_RE: Final = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[^>]+content=[\"'][^\"']*?url=([^\"'>]+)",
    re.IGNORECASE,
)
_JS_REDIRECT_RE: Final = re.compile(
    r"(?:window\.|document\.)?location(?:\.href)?\s*(?:=|\.replace\(|\.assign\()\s*[\"']([^\"']+)[\"']"
)
_TITLE_RE: Final = re.compile(r"<(title|h1)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE: Final = re.compile(r"<[^>]+>")
_HTTP_CLIENT_ERROR: Final = 400
_TIMEOUT: Final = aiohttp.ClientTimeout(total=30)
_TRANSPORT_ERRORS: Final = (TimeoutError, aiohttp.ClientError, json.JSONDecodeError)


class BudgetThuisInvalidLinkError(BudgetThuisAuthError):
    """The pasted login link is malformed, expired or already used."""


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _qp(url: str, key: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(key)
    return values[0] if values else None


def _is_app_redirect(url: str) -> bool:
    return url.lower().startswith(REDIRECT_URI.split("//", 1)[0])


def _unwrap_link(link: str) -> str:
    """Strip whitespace/quotes and unwrap common mail-client redirect wrappers."""
    link = link.strip().strip("<>\"'")
    parsed = urlparse(link)
    host = (parsed.hostname or "").lower()
    # Gmail / Google: https://www.google.com/url?q=<real link>
    if host.endswith("google.com") and parsed.path == "/url":
        inner = _qp(link, "q") or _qp(link, "url")
        if inner:
            return inner
    # Outlook safe links: https://*.safelinks.protection.outlook.com/?url=<real link>
    if host.endswith("safelinks.protection.outlook.com"):
        inner = _qp(link, "url")
        if inner:
            return inner
    return link


def _describe(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}{p.path}"


def _soft_redirect(html: str) -> str | None:
    """Return a meta-refresh or JavaScript redirect target, if the page has one."""
    for regex in (_META_REFRESH_RE, _JS_REDIRECT_RE):
        if m := regex.search(html):
            return unescape(m.group(1).strip())
    return None


def _page_summary(html: str) -> str:
    """Title/heading of a page, for logs (no body text: it may contain PII)."""
    parts = [
        " ".join(_TAG_RE.sub(" ", m.group(2)).split()) for m in _TITLE_RE.finditer(html)
    ]
    forms = [f.action or "(self)" for f in _parse_forms(html)]
    return f"{' | '.join(p for p in parts if p)[:120]} forms={forms}"


@dataclass
class _Form:
    action: str | None = None
    method: str = "get"
    fields: dict[str, str] = field(default_factory=dict)
    has_email: bool = False


class _FormParser(HTMLParser):
    """Collect forms with their named input values (enough for this site)."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[_Form] = []
        self._current: _Form | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._current = _Form(
                action=a.get("action") or None,
                method=(a.get("method") or "get").lower(),
            )
            self.forms.append(self._current)
        elif tag in ("input", "button") and self._current is not None:
            name = a.get("name")
            if a.get("type", "").lower() == "email" or name == "EmailAddress":
                self._current.has_email = True
            if name and (tag == "input" or a.get("value")):
                self._current.fields[name] = a.get("value", "")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def _parse_forms(html: str) -> list[_Form]:
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


class MagicLinkLogin:
    """One login attempt: request a link, then complete it with the pasted URL.

    Keep the same instance between the two steps: it holds the PKCE verifier
    and the cookie jar of the login session.
    """

    def __init__(self, accounts_url: str = ACCOUNTS_URL) -> None:
        """Prepare a fresh PKCE pair and cookie jar."""
        self._accounts = accounts_url.rstrip("/")
        self._verifier, self._challenge = _pkce()
        self._jar = aiohttp.CookieJar()
        self.link_requested_at: float | None = None

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            cookie_jar=self._jar,
            headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
        )

    async def async_request_link(self, email: str) -> None:
        """Start the OAuth flow and ask Budget Thuis to e-mail a login link."""
        try:
            async with self._session() as s:
                # 1. authorize -> (redirects) -> login-link request page
                async with s.get(self._authorize_url()) as r:
                    if r.status != _HTTP_OK:
                        raise BudgetThuisConnectionError(
                            f"authorize: status {r.status}"
                        )
                    page_url = str(r.url)
                    html = await r.text()

                form = next((f for f in _parse_forms(html) if f.has_email), None)
                if form is None:
                    raise BudgetThuisConnectionError(
                        "login page: no e-mail form found (site changed?)"
                    )
                if "__RequestVerificationToken" not in form.fields:
                    raise BudgetThuisConnectionError(
                        "login page: no verification token"
                    )
                data = dict(form.fields)
                data["EmailAddress"] = email
                data.setdefault("Label", LABEL)
                action = urljoin(page_url, form.action or REQUEST_LINK_PATH)

                # 2. request the link (200 "check your mail" page or a redirect)
                async with s.post(action, data=data, allow_redirects=True) as r:
                    if r.status >= _HTTP_CLIENT_ERROR:
                        raise BudgetThuisConnectionError(
                            f"request link: status {r.status}"
                        )
                    body = await r.text()
                # If the same e-mail form comes back, the address was rejected.
                if (
                    any(
                        f.has_email and (f.action or "").endswith(REQUEST_LINK_PATH)
                        for f in _parse_forms(body)
                    )
                    and "field-validation-error" in body
                ):
                    raise BudgetThuisAuthError("e-mail address rejected")
        except _TRANSPORT_ERRORS as err:
            raise BudgetThuisConnectionError(str(err)) from err
        self.link_requested_at = time.time()

    async def async_complete(self, link: str) -> Tokens:
        """Open the pasted login link and exchange the resulting code for tokens."""
        url = _unwrap_link(link)
        if _is_app_redirect(url):
            # The user pasted the final app redirect (e.g. from dev tools).
            return await self._exchange(self._code_from(url))
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            _LOGGER.warning(
                "Pasted login link is not an https link (starts with %r)", url[:12]
            )
            raise BudgetThuisInvalidLinkError("not an https link")

        # Only scheme/host/path are logged: queries carry one-time tokens.
        route: list[str] = []
        try:
            async with self._session() as s:
                try:
                    code = await self._follow(s, url, route)
                except BudgetThuisInvalidLinkError:
                    # The mailed link is a web-portal auto-login: it signs in on
                    # accounts.budgetthuis.nl but returns to mijn.budgetthuis.nl,
                    # not to the app. That sign-in session is now in our cookie
                    # jar, so a fresh app authorize completes without a prompt.
                    route.append("retry app authorize")
                    code = await self._follow(s, self._authorize_url(), route)
                _LOGGER.debug("Login link route: %s", " -> ".join(route))
                return await self._exchange(code, session=s)
        except BudgetThuisInvalidLinkError:
            _LOGGER.warning(
                "Login link did not yield an authorization code. Route: %s",
                " -> ".join(route) or "(none)",
            )
            raise
        except _TRANSPORT_ERRORS as err:
            raise BudgetThuisConnectionError(str(err)) from err

    def _authorize_url(self) -> str:
        query = urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPE,
                "label": LABEL,
                "code_challenge": self._challenge,
                "code_challenge_method": "S256",
            },
            quote_via=quote,
        )
        return f"{self._accounts}/connect/authorize?{query}"

    async def _follow(
        self, s: aiohttp.ClientSession, url: str, route: list[str]
    ) -> str:
        """Follow redirects, meta/JS redirects and confirm forms to the app code."""
        method, data = "get", None
        forms_submitted = 0
        for _ in range(_MAX_HOPS):
            async with s.request(method, url, data=data, allow_redirects=False) as r:
                route.append(f"{method.upper()} {_describe(url)} [{r.status}]")
                if r.status in _REDIRECTS:
                    loc = r.headers.get("Location")
                    if not loc:
                        raise BudgetThuisConnectionError("redirect without Location")
                    if _is_app_redirect(loc):
                        route.append("budgetthuis://login_success")
                        return self._code_from(loc)
                    url, method, data = urljoin(str(r.url), loc), "get", None
                    continue
                if r.status != _HTTP_OK:
                    raise BudgetThuisInvalidLinkError(f"status {r.status}")
                html = await r.text()
                page_url = str(r.url)

            route.append(f"page {_page_summary(html)!r}")
            if target := _soft_redirect(html):
                if _is_app_redirect(target):
                    return self._code_from(target)
                url, method, data = urljoin(page_url, target), "get", None
                continue
            forms = [f for f in _parse_forms(html) if not f.has_email]
            if not forms or forms_submitted >= _MAX_FORMS:
                raise BudgetThuisInvalidLinkError("no authorization code")
            form = forms[0]
            forms_submitted += 1
            url = urljoin(page_url, form.action or page_url)
            method = "post" if form.method == "post" else "get"
            data = dict(form.fields) if method == "post" else None
            if method == "get" and form.fields:
                url = f"{url}{'&' if '?' in url else '?'}{urlencode(form.fields)}"
        raise BudgetThuisConnectionError("too many redirects")

    @staticmethod
    def _code_from(url: str) -> str:
        if error := _qp(url, "error"):
            raise BudgetThuisAuthError(f"authorization error: {error}")
        code = _qp(url, "code")
        if not code:
            raise BudgetThuisInvalidLinkError("no authorization code")
        return code

    async def _exchange(
        self, code: str, session: aiohttp.ClientSession | None = None
    ) -> Tokens:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": self._verifier,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
        }
        try:
            if session is not None:
                return await self._token_request(session, data)
            async with self._session() as s:
                return await self._token_request(s, data)
        except _TRANSPORT_ERRORS as err:
            raise BudgetThuisConnectionError(str(err)) from err

    async def _token_request(self, s: aiohttp.ClientSession, data: dict) -> Tokens:
        async with s.post(
            f"{self._accounts}/connect/token", data=data, allow_redirects=False
        ) as r:
            if r.status in (400, 401, 403):
                # invalid_grant: code expired, already used, or verifier mismatch.
                raise BudgetThuisInvalidLinkError(f"token: status {r.status}")
            if r.status != _HTTP_OK:
                raise BudgetThuisConnectionError(f"token: status {r.status}")
            payload = await r.json()
        try:
            return Tokens.from_payload(payload, time.time())
        except (KeyError, TypeError, ValueError) as err:
            raise BudgetThuisConnectionError("token: malformed payload") from err
