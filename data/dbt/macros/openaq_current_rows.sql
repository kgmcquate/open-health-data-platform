{#
    The clean-layer body every OpenAQ staging model shares.

    Unlike macros/socrata_current_rows.sql, there is no dedupe here: OpenAQ's
    `/v3/locations` raw table is itself a full replace every run
    (ohdp_ingestion.openaq.source -- no per-row cursor, it's a snapshot of
    currently-registered stations, not an appended change log), so RAW
    already holds exactly the current snapshot. Identical reasoning to
    macros/cms_current_rows.sql, which this mirrors.

    Column case: dlt's naming convention (ohdp_ingestion/sql_upper.py,
    ADR-0019) upper-cases every raw column name -- fixed here the same way
    cms_current_rows does, by aliasing every column to its lower-case name.
    Under `execute` the real column list comes off the raw table, so a
    newly-arrived OpenAQ column flows through with no code change. Parsing
    (`dbt parse`, CI, the image build) has no warehouse connection, so it
    falls back to `select *`.

    Call the per-resource model directly -- there's exactly one OpenAQ
    resource ingested today, so unlike `cdc_current_rows`/
    `healthdata_gov_current_rows` there's no per-source wrapper to go through:

        {{ openaq_current_rows('locations') }}
#}
{% macro openaq_current_rows(raw_table) -%}
select
    {%- if execute %}
    {%- set cols = adapter.get_columns_in_relation(source('openaq', raw_table)) %}
    {%- for col in cols if col.column not in ('_dlt_id', '_dlt_load_id') %}
    {{ adapter.quote(col.column) }} as {{ adapter.quote(col.column|lower) }},{% endfor %}
    {%- else %}
    * exclude (_dlt_id, _dlt_load_id),
    {%- endif %}
    {{ dlt_load_id_as_ts() }} as ingest_ts
from {{ source('openaq', raw_table) }}
{%- endmacro %}
