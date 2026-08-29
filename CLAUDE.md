# ha-budget-thuis

Home Assistant (HACS) custom integration exposing **Budget Thuis** dynamic energy data:
hourly electricity prices, daily/month-to-date usage + solar production + cost, monthly
installment, free-energy windows, and contract diagnostics.

Unofficial / not affiliated with Budget Thuis (see README + `brand/README.md`).

## Project context

The API client is the standalone
[`aiobudgetthuis`](https://github.com/jdolieslager/aiobudgetthuis) PyPI package,
consumed via `manifest.json` `requirements`.

The API is unofficial and undocumented; build defensively and expect upstream changes.

## Architecture

```
custom_components/budget_thuis/   # client lives in the aiobudgetthuis PyPI package
├── __init__.py     # async_setup_entry: TokenManager + two coordinators -> runtime_data
├── coordinator.py  # TokenManager (shared login/refresh) + PriceCoordinator (fast) + AccountCoordinator (slow) + RuntimeData
├── config_flow.py  # username/password -> discover contracts -> pick + optional alias; reauth; options
├── entity.py       # base CoordinatorEntity, one DeviceInfo per entry
├── sensor.py       # price sensors + account/usage sensors
├── binary_sensor.py# free-energy active / eligible
├── diagnostics.py  # redacted
├── const.py  manifest.json  strings.json  translations/  quality_scale.yaml
└── brand/          # bundled Budget Thuis icons/logos (HA 2026.3 brands proxy)
tests/              # empty for now (client tests moved to aiobudgetthuis); HA-harness tests are next
```

- Auth: OAuth2 + PKCE via server-rendered login-form replay on `accounts.budgetthuis.nl`;
  data API on `app.api.nutsservices.nl`. Only the **refresh token** is stored (never the password).
- Two coordinators share one `TokenManager` → a single login/refresh serves both.
- Verified running in the owner's HA 2026.8.3 (all sensors populate).

## Current state / what's left

Done: working client + config flow (with contract auto-discovery) + coordinators + sensors +
binary_sensor + diagnostics + brand assets; sensor types reviewed and corrected; verified on
HA 2026.8.3; client extracted to `aiobudgetthuis` (live on PyPI, pinned in `manifest.json`
`requirements`); full HA-harness test suite (29 tests, 100% coverage, config flow included);
tooling (`pyproject.toml`, pre-commit, `requirements_test.txt`) and CI (hacs + hassfest +
lint + test jobs) in place; everything green.

Not done:
- GitHub push + first Release (HACS reads the version from the Release, not the tag).
- Optional: external-statistics for daily usage (backfill yesterday onto the right day).
- Optional: `icon-translations` (Gold rule, still todo in `quality_scale.yaml`).

## House rules

Follow the guidelines in `.claude/guidelines/` — they are the standard for this repo:
- **`comments-and-docs.md`** — comment the way good engineers do: no line-by-line narration;
  comments capture the non-obvious *why*; context lives in docstrings / README / ADRs / commit
  messages / tests. Read this before writing comments.
- **`python-style.md`** — Ruff + mypy (strict), Google docstrings, typing, `pyproject.toml`.
- **`ha-integration.md`** — HA patterns, quality scale, the two-coordinator design.
- **`testing.md`** — `pytest-homeassistant-custom-component`, config-flow 100%, snapshots.
- **`hacs-release.md`** — manifest/hacs.json, brands, CI, versioning, release checklist.

The `.claude/agents/` specialists each map to one of these — prefer delegating focused work to them.

### Always
- Async only — no blocking I/O in the event loop; `aiohttp` via `async_get_clientsession`.
- Type everything; aim to pass strict mypy. `from __future__ import annotations` in every module.
- Store runtime objects on the typed `entry.runtime_data`; store only the refresh token in `data`.
- Keep secrets out of code, logs, diagnostics, tests, and fixtures. Redact tokens/PII.
- Run `ruff check`, `ruff format`, `mypy`, and `pytest` before considering work done.
- Don't regress the reviewed sensor types (price = no device_class + measurement; period energy
  = ENERGY + total + last_reset; period cost = MONETARY + total + last_reset).

### Never
- Real credentials, tokens, or the owner's address/EAN/contract data committed anywhere.
- f-strings in logging; `print`; commented-out code; author/changelog comments.
- Runtime deps in `pyproject.toml` (they go in `manifest.json` `requirements`).

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt   # once it exists; pins PHACC to the HA version
ruff check . && ruff format --check . && mypy custom_components
pytest -q                              # add --cov=custom_components.budget_thuis for coverage
```

## For a fresh session

This repo was scaffolded and implemented across a prior session and is being finalized here to
prep for GitHub. Read the guidelines, finish the "not done" list above (tests + tooling +
client extraction are the priorities for a clean HACS release), keep everything green, and do
not commit or push until asked.
