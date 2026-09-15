-- Mart layer: presentation tables the semantic layer / dashboards read. One
-- schema per mart (mart_respiratory). Stub: weekly state rollup.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    state,
    date_trunc('week', report_date) as week,
    avg(inpatient_beds_used_covid) as avg_covid_inpatients,
    avg(inpatient_beds_used / nullif(inpatient_beds, 0)) as avg_occupancy,
    max(ingest_ts) as ingest_ts
from {{ ref('core__hospital_utilization_daily') }}
group by 1, 2

)

select
    {{ row_sk(['state', 'week']) }} as row_sk,
    *
from mart
