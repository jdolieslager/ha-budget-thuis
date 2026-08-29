# Python style & tooling

Standards for this integration's Python, aligned to Home Assistant core (so it could pass
review) and pleasant to maintain. Verified against HA core's `dev` branch and the HA developer
docs (2026). Sources at the bottom.

## Tooling: Ruff + mypy (no Black, no pylint)

- **Ruff is the single formatter + linter.** Black is fully gone from HA core. Format with
  `ruff format`, lint with `ruff check`. Line length is the formatter default (88); `E501` is
  ignored so long URLs/strings don't fail linting.
- **mypy** for type checking, run strict (see below).
- **codespell** for spelling; **prettier** for json/yaml.
- Skip **pylint** — it relies on HA core's in-repo custom plugins that don't work standalone.
  Ruff's `PL*` families cover the rest.

## `[tool.ruff]` (in `pyproject.toml`)

```toml
[tool.ruff]
required-version = ">=0.16.0"
target-version = "py313"          # bump to py314 when the min HA drops 3.13

[tool.ruff.lint]
select = [
    "A001", "ASYNC", "B", "BLE", "C4", "COM818", "D", "DTZ", "E", "F", "FLY",
    "FURB", "G", "I", "INP", "ISC", "ICN001", "LOG", "N", "PERF", "PGH", "PIE",
    "PL", "PT", "PTH", "PYI", "RET", "RSE", "RUF", "SIM", "SLF", "SLOT", "T20",
    "TC", "TID", "TRY", "UP", "W",
    "ERA001",          # commented-out code (we forbid it — see comments-and-docs.md)
    "TD002", "TD003",  # TODOs must have an author + a ticket link
]
ignore = [
    "D203",    # conflicts with D211
    "D213",    # conflicts with D212
    "E501",    # line length handled by the formatter
    "TRY003",  # long messages outside the exception class (noisy)
    "TID252",  # allow relative imports within the integration package
]

[tool.ruff.lint.isort]
force-sort-within-sections = true
combine-as-imports = true
split-on-trailing-comma = false
known-first-party = ["custom_components.budget_thuis"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.mccabe]
max-complexity = 25

[tool.ruff.format]
# defaults match HA: double quotes, 88 cols, LF.
```

## Typing (aim to pass strict mypy — Platinum `strict-typing`)

- `from __future__ import annotations` at the top of **every** module.
- **PEP 695 `type` aliases** for the config entry: `type BudgetThuisConfigEntry = ConfigEntry[RuntimeData]`.
- `Final` for module constants; `TypedDict` for structured dict payloads; `@dataclass(slots=True, kw_only=True)` for runtime containers.
- **Avoid `Any`.** Narrow with `TYPE_CHECKING` + `isinstance` when unavoidable.
- `@override` (from `typing`) on methods overriding HA base classes.
- Every `# type: ignore` must name its code (`# type: ignore[arg-type]`); no bare `# noqa`.

```toml
[tool.mypy]
python_version = "3.13"
check_untyped_defs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
disallow_untyped_calls = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_return_any = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_unreachable = true
strict_equality = true
enable_error_code = ["ignore-without-code", "redundant-self", "explicit-override"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
```

## Docstrings & comments

Google-style docstrings; type info goes in annotations, not docstrings. Comment philosophy,
the "why not what" rule, and where context belongs are covered in full in
[`comments-and-docs.md`](./comments-and-docs.md) — read it. In short: no line-by-line
narration; comments capture the non-obvious *why*; description/context lives in docstrings,
README, ADRs, commits, and tests.

## Naming & structure

- Constants in `const.py`, `Final`-typed, **alphabetically ordered**; `DOMAIN` is the anchor.
- `_LOGGER = logging.getLogger(__name__)` at module top; private helpers prefixed `_`.
- No wildcard imports. `__all__` only in a shipped library package, not in the integration.
- Module layout: `__init__.py`, `const.py`, `config_flow.py`, `coordinator.py`, `entity.py`,
  one file per platform, `diagnostics.py`, `quality_scale.yaml`, `strings.json`/`translations/`.
  See [`ha-integration.md`](./ha-integration.md).

## The dependency split (important)

- **Runtime dependencies go in `manifest.json` → `requirements`** (PEP 508 pins HA installs),
  **not** in `pyproject.toml`. Our client is currently bundled under
  `custom_components/budget_thuis/api/`; extracting it to a PyPI package + listing it in
  `requirements` is the path to Bronze `dependency-transparency` (see `hacs-release.md`).
- `pyproject.toml` here is for **tooling + dev/test deps only**. `homeassistant` is the host,
  not a runtime dep.

## `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.0
    hooks:
      - id: ruff-check
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.3
    hooks:
      - id: codespell
        args: ["--ignore-words-list=hass", "--skip=*.ambr,*.json"]
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: check-json
      - id: check-yaml
      - id: end-of-file-fixer
      - id: trailing-whitespace
  - repo: local
    hooks:
      - id: mypy
        name: mypy
        entry: mypy
        language: system
        types: [python]
        require_serial: true
```

## Do / Don't

**Do:** `from __future__ import annotations`; type everything; `Final`/`TypedDict`/dataclasses;
constants alphabetized in `const.py`; `_LOGGER.warning("...: %s", value)` (lazy `%s`, no
trailing period, no component name); `entry.runtime_data`; `aiohttp` via
`async_get_clientsession`; `raise ... from err`; `@override`.

**Don't:** Black, f-strings in logging, `print`, `os.path` (use `pathlib`), wildcard imports,
bare `# noqa`/`# type: ignore`, blocking I/O in `async def`, `Any` as an escape hatch, type
info in docstrings, runtime deps in `pyproject.toml`, `hass.data[DOMAIN]` when `runtime_data`
fits, author tags/changelog comments/commented-out code.

## Sources
- HA `pyproject.toml`: https://github.com/home-assistant/core/blob/dev/pyproject.toml
- HA `.pre-commit-config.yaml`: https://github.com/home-assistant/core/blob/dev/.pre-commit-config.yaml
- HA `mypy.ini`: https://github.com/home-assistant/core/blob/dev/mypy.ini
- Style guidelines: https://developers.home-assistant.io/docs/development_guidelines/
- Typing: https://developers.home-assistant.io/docs/development_typing/
- Google-docstring decision (2025-07-03): https://github.com/home-assistant/architecture/discussions/878
- runtime-data rule: https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data
