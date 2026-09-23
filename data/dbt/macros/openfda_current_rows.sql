{#
    The clean-layer body every openFDA staging model shares.

    Dedupe, unlike macros/cms_current_rows.sql: openFDA's enforcement
    endpoints are configured with an `incremental_cursor`/`primary_key`
    (ohdp_orchestration/defs/openfda/datasets/defs.yaml), so each run
    re-fetches a trailing `incremental_lag_days` window and re-lands those
    reports. dlt declares that resource `write_disposition="merge"`, but the
    raw layer is append-only history either way (ADR-0010 -- and see the note
    below on what the Horizon destination actually does with "merge"), so a
    report caught by two runs' windows is two rows in RAW with the same key.
    Reducing to the latest `_dlt_load_id` per key is what makes the clean
    table one row per recall again -- the same `qualify row_number()` shape as
    macros/openaq_current_rows.sql, and for the same reason: recency is
    "which load saw it last", there being no API-side revision timestamp to
    order by first.

    **The key is (recall_number, event_id), not recall_number alone**, and
    the second column is load-bearing rather than belt-and-braces. FDA posts
    an enforcement report before it has assigned a recall number, and fills
    the field with a sentinel until it does: as of this writing drug/
    enforcement carries one row with `recall_number = ''` and one with
    `'N/A'`, and food/enforcement carries one of each too (all four are
    recent, `Not Yet Classified` reports). Partitioning on `recall_number`
    alone would put every sentinel row in one partition and throw away all
    but the newest -- silently dropping real, distinct recalls, and dropping
    more of them the more unnumbered reports FDA has outstanding. `event_id`
    is populated on 100% of rows on all three endpoints (16,888/16,888
    drug, 28,970/28,970 food, 36,231/36,231 device) and differs between those
    sentinel rows, so it separates them. For a row with a real recall number
    it changes nothing: recall_number is already unique there, so event_id is
    functionally determined by it.

    Residual exposure, named rather than hidden: two *products* recalled
    under one unnumbered event would share both columns and still collapse.
    That needs FDA to post two unnumbered rows from one event, and the clean
    layer cannot tell them apart -- there is no other stable identifier on
    the row. core__product_recall's `recall_key` is where the sentinel stops
    being treated as an identifier at all.

    That dedupe is also what makes a status change (`Ongoing` ->
    `Terminated`) visible: the newest load's copy of the report wins, and the
    older copy with the stale status drops out.

    Column case: dlt's naming convention (ohdp_ingestion/sql_upper.py,
    ADR-0019) upper-cases every raw column name -- fixed here the same way
    cms_current_rows/openaq_current_rows do, by aliasing every column to its
    lower-case name. Under `execute` the real column list comes off the raw
    table, so a newly-arrived openFDA field flows through with no code change.
    Parsing (`dbt parse`, CI, the image build) has no warehouse connection, so
    it falls back to `select *`.

    Not handled here: the 21 child tables dlt splits drug/enforcement's nested
    `openfda` object into (DRUG_ENFORCEMENT__OPENFDA__SUBSTANCE_NAME and so
    on). They are undeclared in _stg_openfda__sources.yml and unmodelled --
    see that file's header.

    Call the per-endpoint model directly -- one openFDA resource per
    `raw_table`, so unlike `cdc_current_rows`/`healthdata_gov_current_rows`
    there is no per-source wrapper to go through:

        {{ openfda_current_rows('drug_enforcement') }}
#}
{% macro openfda_current_rows(raw_table, dedupe_by=['recall_number', 'event_id']) -%}
select
    {%- if execute %}
    {%- set cols = adapter.get_columns_in_relation(source('openfda', raw_table)) %}
    {%- for col in cols if col.column not in ('_dlt_id', '_dlt_load_id') %}
    {{ adapter.quote(col.column) }} as {{ adapter.quote(col.column|lower) }},{% endfor %}
    {%- else %}
    * exclude (_dlt_id, _dlt_load_id),
    {%- endif %}
    {{ dlt_load_id_as_ts() }} as ingest_ts
from {{ source('openfda', raw_table) }}
{%- if dedupe_by %}
qualify row_number() over (
    partition by {{ dedupe_by | join(', ') }}
    order by _dlt_load_id desc
) = 1
{%- endif %}
{%- endmacro %}
