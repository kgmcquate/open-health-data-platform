{#
    The clean-layer body every `stg_healthdata_gov__*` model shares.

    RAW is append-only history (ADR-0013), so "clean" means: reduce to the
    latest row per Socrata `:id`, drop dlt's bookkeeping columns, and surface
    the load timestamp as `ingest_ts`. Typing and business logic belong in
    `core` — this stays a `select *` so a newly-arrived Socrata column flows
    through without a code change (paired with
    `on_schema_change="append_new_columns"` in the caller's config).

    Callers supply only the raw table name:

        {{ config(materialized="incremental", incremental_strategy="merge",
                  unique_key="socrata_id", on_schema_change="append_new_columns") }}
        {{ healthdata_gov_current_rows('child_victims_by_age') }}
#}
{% macro healthdata_gov_current_rows(raw_table) -%}
select
    * exclude (_dlt_id, _dlt_load_id),
    {{ dlt_load_id_as_ts() }} as ingest_ts
from {{ source('healthdata_gov', raw_table) }}
qualify row_number() over (
    partition by socrata_id
    order by socrata_updated_at desc, _dlt_load_id desc
) = 1
{%- endmacro %}
