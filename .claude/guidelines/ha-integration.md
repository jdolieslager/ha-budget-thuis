# Home Assistant integration engineering

How this integration must be built, aligned to HA core patterns (`dev` branch, 2026) and the
Integration Quality Scale. Reference integrations to imitate: **tessie** (shared client + N
coordinators), **airgradient** (typed coordinator/entity), **energyzero/nordpool** (Dutch
dynamic prices). Sources at the bottom.

## File layout (`custom_components/budget_thuis/`)

`__init__.py`, `manifest.json`, `const.py`, `config_flow.py`, `coordinator.py`, `entity.py`,
`sensor.py`, `binary_sensor.py`, `diagnostics.py`, `strings.json` + `translations/`,
`quality_scale.yaml`, `brand/`, and (for now) the bundled `api/` client package. The
`common-modules` rule is strict: the coordinator lives in `coordinator.py`, the base entity in
`entity.py` — no exceptions.

## Async rules

- **No blocking I/O in the event loop** — HA 2024.7+ raises on `open`, `time.sleep`,
  `requests`, sync SDKs, etc. inside `async def`. Use `aiohttp`, `asyncio.sleep`, and
  `await hass.async_add_executor_job(...)` only when unavoidable.
- **Inject the shared session** (`inject-websession`, Platinum): build the client with
  `async_get_clientsession(hass)`. The one exception is the login flow, which needs an isolated
  cookie jar (short-lived `aiohttp.ClientSession(cookie_jar=...)`) — keep that scoped to login.
- Wrap every network fetch in `asyncio.timeout(...)` (3.11+ builtin; not the old `async_timeout`).
- Aim for an **async dependency** (Platinum `async-dependency`) — our client is aiohttp-based.

## Config entry: `runtime_data` + typed entry + shared token manager

Store runtime objects on a typed `entry.runtime_data`, never `hass.data[DOMAIN][entry_id]`.
Our shape (mirrors tessie's shared-client, multi-coordinator model):

```python
type BudgetThuisConfigEntry = ConfigEntry[RuntimeData]

@dataclass
class RuntimeData:
    prices: PriceCoordinator      # fast (minutes)
    account: AccountCoordinator   # slow (hours)
    # both hold the same TokenManager so there is one login/refresh, not two
```

`async_setup_entry`: build the `TokenManager`, create both coordinators, call
`await coordinator.async_config_entry_first_refresh()` on each, set `entry.runtime_data`
**before** `async_forward_entry_setups(entry, PLATFORMS)`, and register teardown with
`entry.async_on_unload(...)`. `PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]`.

- **data vs options:** credentials/identity (username, refresh token, contract id) in
  `entry.data`; user-tunable behaviour (intervals, gross/net) in `entry.options`. Never mutate
  either directly — use `hass.config_entries.async_update_entry(...)`.
- **unique_id:** the contract id (globally unique) or a normalized account id — never IP,
  hostname, or URL. `async_set_unique_id(...)` + `_abort_if_unique_id_configured()`.
- **Entry versions:** set `VERSION`/`MINOR_VERSION`; implement `async_migrate_entry` for
  breaking `data` shape changes.

## DataUpdateCoordinator

- Subclass the generic `DataUpdateCoordinator[T]`; pass `config_entry=` into `__init__`; add a
  class-level `config_entry: BudgetThuisConfigEntry` annotation.
- `_async_update_data`: `async with asyncio.timeout(...)`; map auth errors →
  `ConfigEntryAuthFailed` (halts polling, triggers reauth), transient/comms errors →
  `UpdateFailed` (supports `retry_after=`). This gives `entity-unavailable` and
  `log-when-unavailable` for free via `CoordinatorEntity`.
- `always_update=False` when `data` is a dataclass with real `__eq__` (skips redundant writes).
- **Appropriate polling:** fast prices ~15–30 min, slow account ~hours. Don't poll faster than
  the data changes. For the account coordinator, treat the installment call as required (its
  failure surfaces auth/outage) and the rest as best-effort so one flaky endpoint doesn't blank
  everything.
- Use `_async_setup()` for one-time metadata loads; `async_config_entry_first_refresh()` only in
  setup (it's the one that raises `ConfigEntryNotReady`).

## Entities

- Base entity in `entity.py`, `CoordinatorEntity[...]`, `_attr_has_entity_name = True`, one
  `DeviceInfo` per entry with `entry_type=DeviceEntryType.SERVICE` (cloud service, not hardware)
  and `configuration_url`.
- `SensorEntityDescription` subclasses with a `value_fn`; `_attr_unique_id =
  f"{entry.entry_id}_{description.key}"`; `translation_key` for names (English name under
  `entity.sensor.<key>.name` in `strings.json`).
- Don't override `available` — `CoordinatorEntity` already returns `last_update_success`.
- `PARALLEL_UPDATES = 0` on the read-only sensor/binary_sensor platforms.
- `_unrecorded_attributes` for large/noisy attributes (our forecast arrays).
- Sensor-type correctness (device_class/state_class) is decided and documented — see the sensor
  review notes; price = no device_class + measurement + EUR/kWh; period energy = ENERGY + total
  + last_reset; period cost = MONETARY + total + last_reset. Do not regress these.
- `AddConfigEntryEntitiesCallback` is the current `async_add_entities` type.

## Config / reauth / reconfigure / options flow

- `async_step_user`: validate credentials with a live call before creating the entry
  (`test-before-configure`), surfacing `invalid_auth` / `cannot_connect` / `unknown`. Our flow
  then discovers contracts and shows a picker with an optional custom name.
- `async_step_reauth` / `async_step_reauth_confirm`: re-login, `_get_reauth_entry()`,
  `async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: ...})`, guard with
  `_abort_if_unique_id_mismatch`.
- **Options:** use `OptionsFlowWithReload` (2025.8+) — it auto-reloads on change; do NOT pass
  `config_entry` to `OptionsFlow.__init__` or set `self.config_entry`, and
  `async_get_options_flow` takes no args.
- Store only the **refresh token**, not the password.
- `strings.json` is the source; `translations/en.json` is generated from it; reuse common
  strings via `[%key:common::config_flow::...%]`.

## Error handling & logging

- Setup: transient → `ConfigEntryNotReady` (or let `async_config_entry_first_refresh` raise it),
  bad creds → `ConfigEntryAuthFailed`, permanent → `ConfigEntryError`. Always `raise ... from err`.
- Don't log your own `ConfigEntryNotReady`/retry messages (HA handles them).
- `_LOGGER` per module; lazy `%s` formatting; no trailing period; no component name; never log
  secrets. See [`comments-and-docs.md`](./comments-and-docs.md) for the logging rules.
- Gold: translatable exceptions (`translation_domain`/`translation_key`); repairs via
  `issue_registry` only when the user can actually act.

## Diagnostics & security

- `diagnostics.py` with `async_redact_data` and a `TO_REDACT` covering username, password,
  refresh/access tokens.
- `entry.data`/`.options` are persisted **unencrypted** in `.storage/core.config_entries`.
  Minimize what's stored (refresh token over password), keep live token/client objects in
  `runtime_data` (in-memory), never persist tokens to side files, never log or expose them.

## Quality scale — target Bronze first

`quality_scale.yaml` maps each rule to `done` / `todo` / `exempt` (+ comment). Bronze essentials
for us: `config-flow`, `test-before-configure`, `test-before-setup`, `unique-config-entry`,
`runtime-data`, `entity-unique-id`, `has-entity-name`, `appropriate-polling`, `common-modules`,
`brands`, `dependency-transparency` (bundled client → todo: extract to PyPI), and
`config-flow-test-coverage` (100% — see [`testing.md`](./testing.md)). Mark `action-setup`,
`entity-event-setup`, and `docs-actions/-triggers/-conditions` **exempt** with a comment (no
actions/events). Silver adds `reauthentication-flow` (done — we have auth),
`config-entry-unloading`, `entity-unavailable`, `log-when-unavailable`, `parallel-updates`,
`integration-owner`, `test-coverage`.

## Recently changed (don't follow stale tutorials)

- `runtime_data` over `hass.data[DOMAIN]`.
- `async_forward_entry_setups` (plural); singular `async_forward_entry_setup` removed in 2025.6.
- `_attr_has_entity_name = True` mandatory; `translation_key` over hardcoded `name=`.
- `AddConfigEntryEntitiesCallback` replaces `AddEntitiesCallback`.
- `OptionsFlowWithReload` replaces manual `add_update_listener` reloads.
- `_get_reauth_entry()`/`_get_reconfigure_entry()` + `async_update_reload_and_abort(...)`.
- `asyncio.timeout` replaces `async_timeout`.

## Sources
- File structure: https://developers.home-assistant.io/docs/creating_integration_file_structure
- Manifest: https://developers.home-assistant.io/docs/creating_integration_manifest
- Blocking I/O: https://developers.home-assistant.io/docs/asyncio_blocking_operations
- Config entries / flow: https://developers.home-assistant.io/docs/config_entries_index · https://developers.home-assistant.io/docs/core/integration/config_flow
- Fetching data (coordinator): https://developers.home-assistant.io/docs/integration_fetching_data
- Entities: https://developers.home-assistant.io/docs/core/entity
- Quality scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
- `async_forward_entry_setups` blog: https://developers.home-assistant.io/blog/2024/06/12/async_forward_entry_setups/
- References: tessie, airgradient, energyzero, nordpool under https://github.com/home-assistant/core/tree/dev/homeassistant/components/
