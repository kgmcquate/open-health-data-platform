-- Core layer: conformed, typed facts and dimensions, cross-source, the grain
-- downstream marts join on. Full rebuild each run — small tables.
-- Stub: one typed fact off the clean hospital-capacity model.
{{ config(materialized="table") }}

select
    cast(state as varchar)                        as state,
    to_date(date)                                 as report_date,
    inpatient_beds::int                           as inpatient_beds,
    inpatient_beds_used::int                      as inpatient_beds_used,
    inpatient_beds_used_covid::int                as inpatient_beds_used_covid
from {{ ref('stg_healthdata_gov__hospital_capacity_by_state') }}
where state is not null
