-- Clean layer: raw is append-only history; reduce to the current row per
-- socrata_id, drop dlt bookkeeping, keep the source columns as-is (typing and
-- business logic belong in `core`). Merge so re-runs only rewrite what changed
-- (ADR-0012). on_schema_change is explicit: dbt-snowflake's default ("ignore")
-- would silently drop newly-arrived Socrata columns instead of adding them.
{{ config(
    materialized="incremental",
    incremental_strategy="merge",
    unique_key="socrata_id",
    on_schema_change="append_new_columns"
) }}

with ranked as (
    select
        *,
        row_number() over (
            partition by socrata_id
            order by socrata_updated_at desc, _dlt_load_id desc
        ) as _rn
    from {{ source('healthdata_gov', 'covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw') }}
)

select * exclude (_rn, _dlt_id, _dlt_load_id)
from ranked
where _rn = 1
