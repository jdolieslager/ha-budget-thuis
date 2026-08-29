# Budget Thuis — Home Assistant integration

Custom HACS integration exposing **Budget Thuis** dynamic hourly electricity
prices in Home Assistant: current price, component breakdown, daily
aggregates, and the full hourly forecast — ready for the Energy dashboard and
price-based automations.

> **Unofficial.** This is a community-built integration. It is not affiliated
> with, endorsed by, sponsored by, or supported by Budget Thuis. "Budget Thuis"
> and the Budget Thuis logo are trademarks of their respective owner and are used
> here solely to identify the service this integration connects to.

## Install (HACS custom repository)

1. HACS → ⋮ → Custom repositories → add `https://github.com/jdolieslager/ha-budget-thuis`, category **Integration**.
2. Install "Budget Thuis", restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **Budget Thuis**.
4. Enter your account email and password. Your **contract is detected
   automatically** — if you have more than one, you pick it from a list.

Credentials are used once to obtain an OAuth token; only the **refresh token** is
stored (the password is discarded). If the token is ever revoked, HA prompts you
to re-authenticate.

## Entities

One "Budget Thuis" device with:

| Entity | Unit | Notes |
|--------|------|-------|
| Current electricity price | EUR/kWh | all-in; holds `prices_today`/`prices_tomorrow`/`prices` + `tomorrow_valid` attributes |
| Current price commodity / energy tax / surcharge | EUR/kWh | disabled by default |
| Next hour price | EUR/kWh | |
| Average / lowest / highest price today | EUR/kWh | |
| Lowest / highest price time today | timestamp | |
| Percentage of max price | % | |
| Monthly advance amount | EUR | installment; min/max in attributes |
| Consumption / production yesterday | kWh | daily electricity + solar feed-in (T+1) |
| Net cost yesterday | EUR | supplier-reported |
| Consumption / production this month | kWh | month-to-date |
| Net cost this month | EUR | month-to-date |
| Next free energy start / end | timestamp | upcoming free-energy windows (all in attributes) |
| Fixed supply cost / Contract type / Contract start / Meter reading mandate | — | diagnostic |
| **Free energy active / eligible** (binary) | — | free-energy status |

Usage/cost/account data comes from a **separate, slower coordinator** (updates a
few times a day — this data is daily, T+1), so it doesn't affect price polling.
Prices refresh on the interval you configure.

Price sensors use **no `device_class`** (a per-kWh price is a measurement, not a
`monetary` currency value) with `state_class: measurement` — which is exactly
what the Energy dashboard's "use an entity with current price" option needs.

## Energy dashboard

Settings → Dashboards → Energy → Electricity grid → add your consumption sensor →
**Use an entity with current price** → select
the **Current electricity price** sensor of your Budget Thuis device (its
entity ID depends on the name you gave the entry). HA computes cost
automatically.

## Options

Settings → the integration → Configure:
- **Update interval** (minutes, default 30)
- **Price type**: gross (incl. VAT, default) or net (excl. VAT)

## Removal

1. Settings → Devices & Services → **Budget Thuis** → ⋮ → **Delete** (removes the
   entry, its device, and all entities; the stored refresh token is discarded).
2. Optionally revoke the session by changing your Budget Thuis password.
3. To uninstall completely: HACS → Budget Thuis → ⋮ → **Remove**, then restart
   Home Assistant.

## Notes

- The API is unofficial and undocumented. Built defensively (async, token
  refresh + reauth, schema tolerance) but may break if the upstream changes.
- The API client is the standalone
  [`aiobudgetthuis`](https://github.com/jdolieslager/aiobudgetthuis) PyPI package,
  pinned in `manifest.json` `requirements`.
- Async throughout (`aiohttp`) — no blocking calls in the event loop.

## Development

```bash
python -m pip install -r requirements_test.txt
ruff check . && ruff format --check . && mypy custom_components
pytest
```
