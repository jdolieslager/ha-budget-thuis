# Testing

How we test this integration. Target: Bronze `config-flow-test-coverage` (100% of the config
flow) and Silver `test-coverage` (high overall). Uses the same harness as HA core via
`pytest-homeassistant-custom-component` (PHACC). Closest references: **nordpool** (coordinator +
sensor + binary_sensor, aioclient_mock, freezer polling) and **energyzero** (`snapshot_platform`,
client patching). Sources at the bottom.

## The #1 operational rule: pin PHACC to the HA version

PHACC is republished daily and **each release is locked to one exact HA core version**. Pin
PHACC and `homeassistant` in lockstep in `requirements_test.txt` and the CI matrix, and bump
them together. A mismatch breaks fixtures/imports. Import helpers from
`pytest_homeassistant_custom_component.common`, never `tests.common`.

## `requirements_test.txt`

```
pytest-homeassistant-custom-component==<release matching the target HA core version>
pytest-cov
syrupy
freezegun
# plus the integration's own runtime deps (mirror manifest.json requirements)
```

## pytest config (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"     # required by current pytest-asyncio
addopts = "--cov=custom_components.budget_thuis --cov-report=term-missing"
```

## `tests/` layout

```
tests/
├── __init__.py
├── conftest.py
├── fixtures/            # captured JSON API responses (token, contracts, tariff, usage, ...)
├── snapshots/           # syrupy .ambr files (committed)
├── test_config_flow.py
├── test_init.py         # setup / unload / reauth-on-failure
├── test_coordinator.py
├── test_sensor.py
└── test_binary_sensor.py
```

The pure-logic parsing/price-math tests live in the `aiobudgetthuis` package repo alongside the
client; this repo's suite covers the HA integration layer only (harness tests, mocked client).

## `conftest.py` skeleton

```python
"""Common fixtures for the Budget Thuis integration tests."""
from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, CONF_TOKEN
from homeassistant.core import HomeAssistant

from custom_components.budget_thuis.const import DOMAIN, CONF_CONTRACT_ID

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Required so HA loads anything under custom_components/."""
    yield


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Budget Thuis",
        unique_id="15713421",
        data={CONF_USERNAME: "user@example.com", CONF_TOKEN: "refresh-abc", CONF_CONTRACT_ID: "15713421"},
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    with patch("custom_components.budget_thuis.async_setup_entry", return_value=True) as m:
        yield m


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch BudgetThuisClient everywhere it is imported (autospec = signature-safe)."""
    with (
        patch("custom_components.budget_thuis.coordinator.BudgetThuisClient", autospec=True) as c,
        patch("custom_components.budget_thuis.config_flow.BudgetThuisClient", new=c),
    ):
        client = c.return_value
        # set return values / side_effects per test
        yield client
```

## Mocking the API (never hit the network)

- **Patch the client** (energyzero style, cleanest since we own it): `autospec=True`; load
  canned bodies from `tests/fixtures/*.json` via `async_load_json_object_fixture(hass, "...", DOMAIN)`.
- Or **mock at the aiohttp layer** with the `aioclient_mock` fixture (nordpool style) to exercise
  the real client — but our `login()` builds its own cookie-jar session, so `aioclient_mock`
  won't intercept it; prefer client-patching for flow/coordinator tests. Cover the client's own
  HTTP parsing in the standalone logic tests.
- To exercise failures, set `side_effect=BudgetThuisAuthError` / `BudgetThuisConnectionError`.

## Config-flow tests → 100% (the error-recovery idiom)

Drive with `hass.config_entries.flow.async_init` / `async_configure`; assert
`result["type"] is FlowResultType.X`. The pattern that actually reaches 100%: assert the FORM
comes back with `errors`, clear the side effect, resubmit, assert it proceeds. Cover:

- happy path: user → contract-select → `CREATE_ENTRY`;
- parametrized errors on `async_step_user` (`invalid_auth`, `cannot_connect`, `unknown`) **and
  recovery**;
- `already_configured` abort (same unique_id);
- reauth: `mock_config_entry.start_reauth_flow(hass)` → error → recovery → `reauth_successful`,
  assert the entry's token updated and no duplicate entry;
- options flow.

## Coordinator / entity tests (Silver)

`mock_config_entry.add_to_hass(hass)` → `await hass.config_entries.async_setup(entry.entry_id)`
→ `await hass.async_block_till_done()`. Advance polling with the `freezer` fixture:
`freezer.tick(timedelta(...))` **then** `async_fire_time_changed(hass)` **then**
`await hass.async_block_till_done(wait_background_tasks=True)`. Assert:

- a failing update → entities `STATE_UNAVAILABLE`, then recovery on the next poll;
- an auth failure → exactly **one** reauth flow starts (our shared token manager must not spawn
  two) — assert `async_progress()` has one flow with `context["source"] == SOURCE_REAUTH`.

## Snapshot tests (entity registry + state)

Use `snapshot_platform(hass, entity_registry, snapshot, entry.entry_id)`. Requirements: load
**one** platform (`patch("custom_components.budget_thuis.PLATFORMS", ["sensor"])`), all entities
enabled, and **freeze time** (`@pytest.mark.freeze_time("2026-01-15 12:00:00")`) so timestamps
don't churn the `.ambr`. Generate with `pytest tests/ --snapshot-update`, review the diff, and
commit the snapshots.

## Coverage & CI

- Config flow: 100% (`pytest tests/test_config_flow.py --cov=custom_components.budget_thuis.config_flow --cov-report=term-missing`).
- Overall: high (Silver). Add a GitHub Actions `Tests` job that installs `requirements_test.txt`
  and runs `pytest tests/ --cov=custom_components.budget_thuis --cov-report=term-missing`,
  alongside the existing hassfest + HACS validation jobs (see [`hacs-release.md`](./hacs-release.md)).

## Gotchas

- `enable_custom_integrations` (autouse) is mandatory. If using `recorder_mock`, request it
  **before** `enable_custom_integrations`.
- Never make real network calls; the harness flags outbound sockets.
- `freezer.tick` does nothing without a following `async_fire_time_changed(hass)`.
- Freeze time around snapshots; one platform per `snapshot_platform`; all entities enabled.

## Sources
- Testing docs: https://developers.home-assistant.io/docs/development_testing/
- config-flow-test-coverage: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage/
- test-coverage: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/test-coverage/
- PHACC: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
- References: nordpool & energyzero tests under https://github.com/home-assistant/core/tree/dev/tests/components/
