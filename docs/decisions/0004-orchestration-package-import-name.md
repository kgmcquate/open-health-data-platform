# 0004 — Remap the `data/dagster` import name to `ohdp_orchestration`

**Status:** Accepted

## Context

ARCHITECTURE.md §7 specifies the folder `data/dagster/` for the Dagster code
location. If that directory is importable as a top-level package literally named
`dagster`, it shadows the `dagster` library on `sys.path` and every
`import dagster` in the code location breaks.

## Decision

Keep the folder name `data/dagster/` exactly as the architecture doc specifies.
Map it to the import name `ohdp_orchestration` via Hatch build sources in
`data/pyproject.toml`:

```toml
[tool.hatch.build.targets.wheel.sources]
"ingestion" = "ohdp_ingestion"
"dagster"   = "ohdp_orchestration"
"ml"        = "ohdp_ml"
```

`definitions.py` is loaded by module path (`ohdp_orchestration.definitions`).

## Consequences

- Folder layout matches the architecture doc; import names do not collide.
- Anyone reading `data/dagster/` must know the import alias — noted in that
  package's docstring and here.
- Alternative rejected: renaming the folder to `data/orchestration/`. It would
  drift from the architecture doc for no real gain.
