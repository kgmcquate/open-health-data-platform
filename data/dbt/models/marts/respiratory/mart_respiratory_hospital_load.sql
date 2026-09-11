-- Mart layer: presentation tables the semantic layer / dashboards read. One
-- schema per mart (mart_respiratory). Stub: weekly state rollup.
{{ config(materialized="table") }}

select
    state,
    date_trunc('week', report_date)                    as week,
    avg(inpatient_beds_used_covid)                      as avg_covid_inpatients,
    avg(inpatient_beds_used / nullif(inpatient_beds, 0)) as avg_occupancy
from {{ ref('core_hospital_utilization_daily') }}
group by 1, 2
