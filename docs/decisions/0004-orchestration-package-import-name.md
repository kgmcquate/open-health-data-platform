# 0004 — Orchestration package is imported as `ohdp_orchestration`

**Status:** Accepted
**Amended:** 2026-09-09 — mechanism changed from a Hatch `sources` remap to a
plain src layout (see below).

## Context

ARCHITECTURE.md §7 originally specified the folder `data/dagster/` for the
Dagster code location. A top-level package literally named `dagster` shadows the
`dagster` library on `sys.path` and every `import dagster` in the code location
breaks.

## Decision

The three importable packages under `data/` are named for their import names —
`ohdp_ingestion`, `ohdp_orchestration`, `ohdp_ml` — and live in a src layout,
exactly like `packages/shared` (`src/ohdp_shared`) and `apps/api` (`src/hub_api`):

```
data/
├── src/
│   ├── ohdp_ingestion/
│   ├── ohdp_orchestration/     # definitions.py, assets/, jobs/, schedules/, sensors/, resources/
│   └── ohdp_ml/
├── dbt/
└── pyproject.toml
```

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/ohdp_ingestion", "src/ohdp_orchestration", "src/ohdp_ml"]
```

`definitions.py` is loaded by module path (`ohdp_orchestration.definitions`).

### Why not the earlier Hatch `sources` remap

The first version of this ADR kept the folders as `data/ingestion`,
`data/dagster`, `data/ml` and remapped the import names with:

```toml
[tool.hatch.build.targets.wheel.sources]
"dagster" = "ohdp_orchestration"
```

Hatchling refuses this for **editable** installs: a `sources` rewrite that
*changes* a path prefix rather than *removing* one is unsupported
(`editables` issue #20). `uv sync` therefore failed for the whole workspace, and
only the `--no-editable` Docker build worked. A src layout removes the `src`
prefix — which is supported — so editable installs work everywhere.

## Consequences

- Folder names match import names; no alias to remember, nothing shadows
  `dagster`.
- `data/` now matches the src layout used by the other two Python packages.
- ARCHITECTURE.md §7 updated to show `data/src/ohdp_*`.
