# 0014 — Snowflake-only: drop the local/CI DuckDB targets

**Status:** Accepted
**Relates to:** supersedes the local/prod DuckDB-vs-Snowflake split
[ADR-0012](0012-native-snowflake-tables.md) declared "the permanent
architecture," and drops the DuckDB-conditional branches ADR-0013 introduced
in `generate_database_name.sql` and the generated
`_healthdata_gov__sources.yml`. The medallion layout, per-layer database
split, and schema naming are unchanged — this ADR only removes the second
dialect everything had to stay portable across.

## Context

ADR-0012 accepted two SQL dialects — dbt-duckdb (`local`/`ci` targets) for a
free, offline dev loop, dbt-snowflake (`prod`) for the real warehouse — "as an
accepted, ongoing cost." In practice that cost kept compounding rather than
staying flat:

- `dbt/target/manifest.json` was always compiled against `ci`/`local`
  (Dockerfile, CI, `dagster dev`), never `prod` — so every dbt-derived Dagster
  asset key was computed from whichever DuckDB file's catalog name happened to
  be in play (`build`, `ci_build`), not the real per-layer Snowflake database
  ADR-0013 introduced. Working around that mismatch meant reading a model's
  configured `+database` instead of trusting the compiled one
  (`warehouse/component.py`), and weakening a test's assertion to a prefix
  check instead of an exact key (`test_warehouse_dbt.py`).
- ADR-0013's per-layer database split doesn't exist on DuckDB (a single-file
  catalog), so `generate_database_name.sql` and the generated dbt source both
  needed a `target.type == 'snowflake'` branch, and the local dev loop
  silently blurred `RAW`/`CLEAN` into one file/schema — a correctness gap
  that only stayed harmless by accident (no name overlap yet).
- The raw loader carried the equivalent branch on the dlt side
  (`is_snowflake_configured()` picking a local DuckDB destination), so the
  same "which warehouse am I actually writing to" question had to be answered
  independently in two places.

None of this was caught by tests that actually run against Snowflake, because
nothing in CI or local dev ever did.

## Decision

Delete the DuckDB targets and destinations outright. dbt has one target
(`prod` in `profiles.yml`, `type: snowflake`); dlt's raw loader always writes
to Snowflake. `dbt build`/`dbt test` and dlt's raw loads now need real
Snowflake credentials to run anywhere, local dev included.

`dbt parse` (compiling the manifest without connecting) stays the one
offline-safe operation, since it's what `warehouse/component.py` needs
(`dbt/target/manifest.json`) and what the image, CI, and
`data/tests/conftest.py` all rely on without secrets. `profiles.yml`'s
`env_var()` calls each get an empty-string default so parsing keeps working
with no credentials configured; `dbt build`/`run` still fail loudly against an
empty account/user/key, same as any other missing-credential failure would.

CI's `dbt` job (and the Makefile's `dbt-parse` target, and the image's build
step) becomes parse-only — `dbt parse` against the one target — rather than
`dbt build`/`test`. The pre-merge dbt test gate ARCHITECTURE.md §3 described
is dropped for now rather than replaced with a live-Snowflake CI job; wiring
CI to a real (dev) Snowflake account is a separate decision if wanted later.

## Consequences

- **Deleted**: `profiles.yml`'s `local`/`ci` duckdb outputs and the
  `OHDP_DUCKDB_PATH` env var; the `target.type` branch in
  `generate_database_name.sql`; the Jinja branch in the generated
  `_healthdata_gov__sources.yml` (and its generator,
  `scripts/scrape_healthdata_gov.py`); `ohdp_shared.settings.duckdb_path` and
  `is_snowflake_configured()`; the DuckDB branch in
  `ohdp_ingestion/healthdata_gov/source.py`'s `_destination()`; the
  `environment`-based `--target` selection in
  `warehouse/component.py::DbtWarehouse.build_defs`; the `dbt-duckdb` and
  `duckdb` dependencies (`data/pyproject.toml`) and dlt's `duckdb` extra.
- **The asset-key mismatch ADR-0013 accepted is gone.** `_warehouse_raw_key`
  in `healthdata_gov/component.py` now equals dbt's actual compiled
  source-node key, since the manifest is always parsed against the real
  target; `warehouse/component.py`'s `_Translator.get_asset_key` no longer
  needs the `+database`-config workaround, and `test_warehouse_dbt.py`'s
  prefix-only assertion can be tightened to an exact key.
- **Local dev and tests need Snowflake credentials.** `data/tests/test_raw_load.py`
  can no longer verify a raw load by opening a local DuckDB file directly —
  it needs reworking (mocking the Snowflake destination, or another
  verification strategy) to run without live credentials.
- **CI no longer runs `dbt build`/`dbt test`.** `dbt parse` still validates
  the project compiles on every push; catching a broken model or failing test
  before Snowflake does is deferred until CI has its own Snowflake access.
- **Existing local DuckDB files are dead weight**, not migrated —
  `*.duckdb`/`*.duckdb.wal` can be deleted from any local checkout; the
  `.gitignore`/`.dockerignore` entries for them can go too once nothing
  produces new ones.
