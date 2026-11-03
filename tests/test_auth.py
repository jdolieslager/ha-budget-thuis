"""Tests for the magic-link login against a fake accounts server.

The fake mimics the real page structure: /connect/authorize redirects to
/inloglink-aanvragen, which renders a form with EmailAddress, ReturnUrl, Label
and an antiforgery token. The e-mailed link is a one-time URL that sets the
login cookie and redirects to the OAuth callback, which redirects to the
budgetthuis:// app scheme with a code. All values are fictional.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import quote

import aiohttp
from aiohttp import web
import pytest

from custom_components.budget_thuis.auth import (
    BudgetThuisInvalidLinkError,
    MagicLinkLogin,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

TOKEN = "fake-antiforgery"


class FakeAccounts:
    """Minimal fake of accounts.budgetthuis.nl."""

    def __init__(
        self,
        confirm_page: bool = False,
        meta_refresh: bool = False,
        web_portal: bool = False,
    ) -> None:
        self.web_portal = web_portal
        self.confirm_page = confirm_page
        self.meta_refresh = meta_refresh
        self.challenge: str | None = None
        self.sent_to: str | None = None
        self.return_url: str | None = None
        self.used = False
        self.app = web.Application()
        self.app.router.add_get("/connect/authorize", self.authorize)
        self.app.router.add_get("/inloglink-aanvragen", self.link_page)
        self.app.router.add_post("/inloglink-aanvragen", self.link_post)
        self.app.router.add_get("/inloggen/link", self.magic)
        self.app.router.add_route("*", "/inloggen/bevestigen", self.confirm)
        self.app.router.add_get("/connect/authorize/callback", self.callback)
        self.app.router.add_post("/connect/token", self.token)
        self.app.router.add_get("/portal", self.portal)

    async def authorize(self, request: web.Request) -> web.Response:
        self.challenge = request.query["code_challenge"]
        if request.cookies.get("idsrv") == "session":
            raise web.HTTPFound("budgetthuis://login_success?code=the-code")
        ret = "/connect/authorize/callback?" + request.query_string
        raise web.HTTPFound(f"/inloglink-aanvragen?returnUrl={quote(ret, safe='')}")

    async def link_page(self, request: web.Request) -> web.Response:
        resp = web.Response(
            content_type="text/html",
            text=f"""<html><body>
<form action="/inloglink-aanvragen" method="post">
<input type="hidden" id="ReturnUrl" name="ReturnUrl"
 value="{request.query["returnUrl"].replace("&", "&amp;")}">
<input type="hidden" id="Label" name="Label" value="BudgetEnergie">
<input type="email" id="EmailAddress" name="EmailAddress">
<button type="submit">Stuur inloglink</button>
<input name="__RequestVerificationToken" type="hidden" value="{TOKEN}">
</form></body></html>""",
        )
        resp.set_cookie("af", "1")
        return resp

    async def link_post(self, request: web.Request) -> web.Response:
        data = await request.post()
        assert data["__RequestVerificationToken"] == TOKEN
        assert request.cookies.get("af") == "1"
        self.sent_to = data["EmailAddress"]
        self.return_url = data["ReturnUrl"]
        return web.Response(content_type="text/html", text="<p>Check je mail</p>")

    async def magic(self, request: web.Request) -> web.Response:
        if self.used or request.query.get("token") != "magic":
            return web.Response(content_type="text/html", text="<p>Link verlopen</p>")
        self.used = True
        if self.web_portal:
            # Real behaviour: the link signs in for the web portal and lands
            # on its overview page, never returning to the app.
            resp = web.HTTPFound("/portal")
            resp.set_cookie("idsrv", "session")
            raise resp
        if self.meta_refresh:
            resp = web.Response(
                content_type="text/html",
                text='<meta http-equiv="refresh" content="0;url=/inloggen/bevestigen">',
            )
            resp.set_cookie("idsrv", "session")
            return resp
        if self.confirm_page:
            return web.Response(
                content_type="text/html",
                text='<form method="post" action="/inloggen/bevestigen">'
                '<input type="hidden" name="x" value="1"><button>Inloggen</button>'
                "</form>",
            )
        resp = web.HTTPFound(self.return_url)
        resp.set_cookie("idsrv", "session")
        raise resp

    async def portal(self, request: web.Request) -> web.Response:
        return web.Response(content_type="text/html", text="<title>Overzicht</title>")

    async def confirm(self, request: web.Request) -> web.Response:
        if self.meta_refresh:
            raise web.HTTPFound(self.return_url)
        resp = web.HTTPFound(self.return_url)
        resp.set_cookie("idsrv", "session")
        raise resp

    async def callback(self, request: web.Request) -> web.Response:
        if request.cookies.get("idsrv") != "session":
            raise web.HTTPFound("/inloglink-aanvragen")
        raise web.HTTPFound("budgetthuis://login_success?code=the-code&state=x")

    async def token(self, request: web.Request) -> web.Response:
        data = await request.post()
        digest = hashlib.sha256(data["code_verifier"].encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if data["code"] != "the-code" or challenge != self.challenge:
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response(
            {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}
        )


async def _serve(fake: FakeAccounts) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(fake.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    return runner, f"http://127.0.0.1:{port}"


@pytest.mark.parametrize(
    "mode", ["plain", "confirm_page", "meta_refresh", "web_portal"]
)
async def test_magic_link_login(mode: str) -> None:
    """Request a link, paste it, and get tokens."""
    fake = FakeAccounts(**({mode: True} if mode != "plain" else {}))
    runner, base = await _serve(fake)
    try:
        login = _login(base)
        await login.async_request_link("user@example.com")
        assert fake.sent_to == "user@example.com"
        assert fake.return_url.startswith("/connect/authorize/callback?")

        # The real link is https; the fake runs on http, so patch the check.
        link = f"{base}/inloggen/link?token=magic"
        tokens = await _complete_http(login, link)
        assert tokens.access_token == "acc"
        assert tokens.refresh_token == "ref"
    finally:
        await runner.cleanup()


async def test_used_link_is_rejected() -> None:
    """A link that was already used (or expired) gives invalid_link."""
    fake = FakeAccounts()
    fake.used = True
    runner, base = await _serve(fake)
    try:
        login = _login(base)
        await login.async_request_link("user@example.com")
        with pytest.raises(BudgetThuisInvalidLinkError):
            await _complete_http(login, f"{base}/inloggen/link?token=magic")
    finally:
        await runner.cleanup()


async def test_non_link_input_is_rejected() -> None:
    """Pasting something that is not a link fails fast."""
    login = MagicLinkLogin("http://127.0.0.1:9")
    with pytest.raises(BudgetThuisInvalidLinkError):
        await login.async_complete("not a link")


def _login(base: str) -> MagicLinkLogin:
    login = MagicLinkLogin(base)
    # The fake runs on an IP address; aiohttp drops IP cookies unless unsafe.
    login._jar = aiohttp.CookieJar(unsafe=True)  # noqa: SLF001
    return login


async def _complete_http(login: MagicLinkLogin, link: str):
    """Run async_complete against the http fake (production requires https)."""
    https_link = "https" + link.removeprefix("http")
    real = login._session  # noqa: SLF001

    def session():
        s = real()
        orig_request = s.request

        def request(method, url, *args, **kwargs):
            url = str(url).replace("https://", "http://", 1)
            return orig_request(method, url, *args, **kwargs)

        s.request = request
        return s

    login._session = session  # noqa: SLF001
    return await login.async_complete(https_link)
