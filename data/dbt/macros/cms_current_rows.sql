{#
    The clean-layer body every CMS staging model shares.

    Unlike macros/socrata_current_rows.sql, there is no dedupe here: CMS's raw
    table is itself a full replace every run (ADR-0026 -- no per-row
    `:id`/`:updated_at`, dlt's `write_disposition="replace"` in
    ohdp_ingestion.cms.source), so RAW already holds exactly the current
    snapshot. "Clean" here means only: drop dlt's bookkeeping columns, surface
    the load timestamp as `ingest_ts`, and fix column case.

    Column case: dlt's naming convention (ohdp_ingestion/sql_upper.py,
    ADR-0019) upper-cases every raw column name -- Snowflake's
    unquoted-identifier convention. A `select *` passes that case straight
    through into the clean Iceberg table, and Cube's quoted SQL then can't
    resolve it (see macros/socrata_current_rows.sql's column-case note for the
    full failure mode -- identical here). Fix: alias every column to its
    lower-case name. Under `execute` the real column list comes off the raw
    table, so a newly-arrived CMS column flows through with no code change.
    Parsing (`dbt parse`, CI, the image build) has no warehouse connection, so
    it falls back to `select *`.

    Call the per-dataset model directly -- there's exactly one CMS domain, so
    unlike `cdc_current_rows`/`healthdata_gov_current_rows` there's no
    per-source wrapper to go through:

        {{ cms_current_rows('medicare_part_d_spending_by_drug') }}
#}
{% macro cms_current_rows(raw_table) -%}
select
    {%- if execute %}
    {%- set cols = adapter.get_columns_in_relation(source('cms', raw_table)) %}
    {%- for col in cols if col.column not in ('_dlt_id', '_dlt_load_id') %}
    {{ adapter.quote(col.column) }} as {{ adapter.quote(col.column|lower) }},{% endfor %}
    {%- else %}
    * exclude (_dlt_id, _dlt_load_id),
    {%- endif %}
    {{ dlt_load_id_as_ts() }} as ingest_ts
from {{ source('cms', raw_table) }}
{%- endmacro %}
