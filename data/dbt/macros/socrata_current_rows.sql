{#
    The clean-layer body every Socrata staging model shares, for any domain
    (ADR-0018 made the ingestion side domain-agnostic; this is the read side).

    RAW is append-only history (ADR-0019), so "clean" means: reduce to the
    latest row per Socrata `:id`, drop dlt's bookkeeping columns, and surface
    the load timestamp as `ingest_ts`. Typing and business logic belong in
    `core` -- this stays schema-agnostic (see the column-case note below) so a
    newly-arrived Socrata column flows through without a code change (dlt
    evolves the raw Iceberg table's schema on every append).

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
{#
    Column case: dlt's naming convention (ohdp_ingestion/sql_upper.py, ADR-0019)
    upper-cases every raw column name -- Snowflake's unquoted-identifier
    convention. DuckDB resolves references case-insensitively, so reading them
    works, but a `select *` PASSES THE SOURCE CASE THROUGH into the clean
    Iceberg table, and curated models then pass it through again for any column
    they don't alias. The result is a CURATED mart whose physical columns are
    mixed-case -- `YEAR_SEASON` next to `program` -- while dbt's manifest (and
    therefore Cube, via globals.py's quoted `{CUBE}."year_season"` SQL) says
    lowercase. Snowflake resolves QUOTED identifiers case-sensitively, so Cube
    queries die with "invalid identifier 'year_season'" on exactly the
    passthrough columns.

    Fix: alias every column to its lower-case name here. Under `execute` the
    real column list comes off the raw table (adapter.get_columns_in_relation),
    so a newly-arrived Socrata column still flows through with no code change
    -- the same property `select *` had. Parsing (`dbt parse`, CI, the image
    build) has no warehouse connection, so it falls back to `select *`.
#}
{% macro socrata_current_rows(source_name, raw_table) -%}
select
    {%- if execute %}
    {%- set cols = adapter.get_columns_in_relation(source(source_name, raw_table)) %}
    {%- for col in cols if col.column not in ('_dlt_id', '_dlt_load_id') %}
    {{ adapter.quote(col.column) }} as {{ adapter.quote(col.column|lower) }},{% endfor %}
    {%- else %}
    * exclude (_dlt_id, _dlt_load_id),
    {%- endif %}
    {{ dlt_load_id_as_ts() }} as ingest_ts
from {{ source(source_name, raw_table) }}
qualify row_number() over (
    partition by socrata_id
    order by socrata_updated_at desc, _dlt_load_id desc
) = 1
{%- endmacro %}
