-- Clean layer: raw is append-only history; reduce to the current row per
-- socrata_id, drop dlt bookkeeping, keep the source columns as-is (typing and
-- business logic belong in `core`). Upsert into Iceberg so re-runs only rewrite
-- what changed (ADR-0010).
{{ config(
    materialized="external",
    plugin="iceberg",
    iceberg_strategy="upsert",
    unique_key="socrata_id"
) }}

with ranked as (
    select
        *,
        row_number() over (
            partition by socrata_id
            order by socrata_updated_at desc, _dlt_load_id desc
        ) as _rn
    from {{ source('healthdata_gov', 'raw_healthdata_gov__covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw') }}
)

select * exclude (_rn, _dlt_id, _dlt_load_id)
from ranked
where _rn = 1
