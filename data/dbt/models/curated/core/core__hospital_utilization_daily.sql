-- Core layer: conformed, typed facts and dimensions, cross-source, the grain
-- downstream marts join on. Full rebuild each run — small tables.
-- Stub: one typed fact off the clean hospital-capacity model.
{{ config(materialized="table") }}

select
    cast(state as varchar)                        as state,
    to_date(date)                                 as report_date,
    try_cast(inpatient_beds as double)            as inpatient_beds,
    try_cast(inpatient_beds_used as double)       as inpatient_beds_used,
    try_cast(inpatient_beds_used_covid as double) as inpatient_beds_used_covid
from {{ ref('stg_healthdata_gov__hospital_capacity_by_state') }}
where state is not null
