{#
    The clean-layer body every Socrata staging model shares, for any domain
    (ADR-0018 made the ingestion side domain-agnostic; this is the read side).

    RAW is append-only history (ADR-0019), so "clean" means: reduce to the
    latest row per Socrata `:id`, drop dlt's bookkeeping columns, and surface
    the load timestamp as `ingest_ts`. Typing and business logic belong in
    `core` -- this stays a `select *` so a newly-arrived Socrata column flows
    through without a code change (dlt evolves the raw Iceberg table's schema
    on every append).

    **Full rebuild, not incremental.** The dedupe is a `qualify` over the whole
    raw history, so the model was never compute-incremental even when it was
    materialized that way (ADR-0019) -- only the *write* was. Rebuilding the
    table is what DuckDB's Iceberg writer does most reliably (CREATE TABLE AS
    then rename, no MERGE), and the raw tables are small. Revisit if a raw
    table ever gets big enough for the rewrite to hurt.

    Call the per-source wrapper (`cdc_current_rows`,
    `healthdata_gov_current_rows`), not this, so the source name can't be
    mistyped:

        {{ cdc_current_rows('nndss_weekly_data') }}
#}
{% macro socrata_current_rows(source_name, raw_table) -%}
select
    * exclude (_dlt_id, _dlt_load_id),
    {{ dlt_load_id_as_ts() }} as ingest_ts
from {{ source(source_name, raw_table) }}
qualify row_number() over (
    partition by socrata_id
    order by socrata_updated_at desc, _dlt_load_id desc
) = 1
{%- endmacro %}
