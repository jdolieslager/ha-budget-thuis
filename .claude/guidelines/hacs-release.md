# HACS packaging & release

How this repo is packaged, validated, and released for HACS. Sources at the bottom.

## Repo shape

- Public GitHub repo, description + topics + README, **one** integration under
  `custom_components/budget_thuis/`.
- `hacs.json` at the repo root:
  ```json
  { "name": "Budget Thuis", "homeassistant": "2024.12.0", "country": "NL" }
  ```
  Set `homeassistant` to the minimum HA release we actually rely on.
- `manifest.json` must include (custom-integration + HACS requirements): `domain`, `name`,
  `codeowners`, `documentation`, `issue_tracker`, `integration_type` (`service`), `iot_class`
  (`cloud_polling`), `config_flow: true`, `requirements`, `version`. Omit `quality_scale` only if
  you don't want to advertise a tier (we set `bronze`).

## Versioning & releases

- **HACS reads the version from the latest GitHub Release, not from tags alone.** A tag without
  a Release is invisible to HACS (it falls back to the default branch). Every release:
  1. bump `manifest.json` `version` (SemVer),
  2. tag matching that version,
  3. publish a GitHub **Release** with notes.
- Consider Conventional Commits + release-please/`release-drafter` to automate this later.

## Brand assets (already in place)

- Bundled in `custom_components/budget_thuis/brand/` per the HA 2026.3 Brands Proxy API — no
  `home-assistant/brands` PR needed for custom integrations (that path is now legacy/auto-closed).
- Files: `icon.png` (256) + `icon@2x.png` (512), `logo.png`/`logo@2x.png`, and `dark_` variants.
  Official Budget Thuis marks, unmodified (dark wordmark is a color-only white variant). PNG,
  transparent, trimmed. See `brand/README.md` for provenance + the trademark notice.
- Known quirk: HACS' own download list may show "icon not available" (it reads the HACS CDN);
  the icon renders correctly inside HA. Tracked upstream (hacs/integration #5223).

## CI validation

Two jobs (plus the test job from [`testing.md`](./testing.md)):

```yaml
name: Validate
on:
  push:
  pull_request:
  schedule: [{ cron: "0 0 * * *" }]
  workflow_dispatch:
jobs:
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with: { category: integration }
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
```

`hassfest` validates the manifest and (if present) that `quality_scale.yaml` matches implemented
rules. The HACS action checks repo metadata, brands, description, topics, `hacs.json`.

## Distribution paths

- **Custom repository (immediate):** users add the repo URL in HACS → works now, no approval.
- **HACS default store (later):** PR to `hacs/default`; requires passing HACS action + hassfest,
  a real Release, brand assets, submitted from a personal account; review can take months.
- The HACS default store is separate from — and far easier than — getting merged into
  `home-assistant/core`.

## Pre-release checklist

- [ ] `ruff check` + `ruff format --check` clean; `mypy` clean.
- [ ] `pytest` green; config-flow coverage 100%.
- [ ] hassfest + HACS action green in CI.
- [ ] `manifest.json` `version` bumped; `requirements` pins a real release of the client.
- [ ] `README` has install + config + removal instructions and the unaffiliated/trademark notice.
- [ ] `quality_scale.yaml` accurate (done/exempt/todo with comments).
- [ ] GitHub Release published with the matching tag.
- [ ] Secrets check: nothing real in the repo (no tokens/credentials/PII in code, docs, fixtures).

## Sources
- HACS publish: https://www.hacs.xyz/docs/publish/start/ · /integration/ · /include/ · /action/
- Brands proxy (2026.3): https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api/
- Quality scale: https://developers.home-assistant.io/docs/core/integration-quality-scale/
