{#
    The clean-layer body every OpenAQ staging model shares.

    `locations` needs no *cross-load* dedupe: it's itself a full replace every
    run (ohdp_ingestion.openaq.source -- no per-row cursor, it's a snapshot of
    currently-registered stations, not an appended change log), so RAW already
    holds exactly the current snapshot -- identical reasoning to
    macros/cms_current_rows.sql. It still passes `dedupe_by=['id']`, because
    the paged locations walk can return the same station twice inside one
    snapshot; see that model for the detail.

    `monthly_measurements` does need one: it's appended over a trailing
    lookback window (ohdp_ingestion.openaq.source's `lookback_months`), so a
    revised month lands as a *new* row rather than overwriting the old one in
    RAW. Pass `dedupe_by` (the natural key, e.g. `['sensor_id',
    'period_label']`) to reduce to the latest `_dlt_load_id` per key -- same
    `qualify row_number()` shape as macros/socrata_current_rows.sql, just
    without an API-side `updated_at` to order by first (OpenAQ's monthly
    rollup has none -- see the source module's docstring), so recency is
    purely "which load saw it last."

    Column case: dlt's naming convention (ohdp_ingestion/sql_upper.py,
    ADR-0019) upper-cases every raw column name -- fixed here the same way
    cms_current_rows does, by aliasing every column to its lower-case name.
    Under `execute` the real column list comes off the raw table, so a
    newly-arrived OpenAQ column flows through with no code change. Parsing
    (`dbt parse`, CI, the image build) has no warehouse connection, so it
    falls back to `select *`.

    Call the per-resource model directly -- there's exactly one OpenAQ
    resource per `raw_table` today, so unlike `cdc_current_rows`/
    `healthdata_gov_current_rows` there's no per-source wrapper to go through:

        {{ openaq_current_rows('locations') }}
        {{ openaq_current_rows('monthly_measurements', dedupe_by=['sensor_id', 'period_label']) }}
#}
{% macro openaq_current_rows(raw_table, dedupe_by=none) -%}
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
{%- if dedupe_by %}
qualify row_number() over (
    partition by {{ dedupe_by | join(', ') }}
    order by _dlt_load_id desc
) = 1
{%- endif %}
{%- endmacro %}
