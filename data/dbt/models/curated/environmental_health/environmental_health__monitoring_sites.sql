-- Mart layer: the air-quality monitoring network itself, at station grain --
-- one pin per station on a map, and the denominator behind any "how much of
-- the country do we actually observe" question.
--
-- Grain: location_id.
--
-- Restricted to stations OpenAQ flags as government reference monitors, and
-- to the countries the ingestion covers. Low-cost sensor nodes are in
-- core__air_monitoring_site but not here: the measurement fan-out never
-- visits them (ohdp_ingestion.openaq.source's `reference_monitors_only`), so
-- including them would put thousands of pins on a map that no reading in
-- environmental_health__air_quality_monthly can ever correspond to.
--
-- has_measurements is the join back to the fact and the caveat that matters
-- most on this table: even among reference monitors, the monthly fan-out is
-- capped (`max_locations`/`max_sensors`), so a station can be indexed here
-- and still have no readings ingested. Filter on it before treating this as
-- the set of stations behind a chart.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with measured as (

    select
        location_id,
        count(distinct parameter)  as parameter_count,
        min(month_start)            as first_month_with_readings,
        max(month_start)            as last_month_with_readings
    from {{ ref('core__air_quality_monthly') }}
    group by 1

), mart as (

select
    s.location_id,
    s.location_name,
    s.locality,
    s.country_code,
    s.country_name,
    s.latitude,
    s.longitude,
    s.provider_name,
    s.owner_name,
    s.timezone,
    s.is_mobile,
    s.first_measurement_at_utc,
    s.last_measurement_at_utc,
    m.location_id is not null                    as has_measurements,
    coalesce(m.parameter_count, 0)               as parameter_count,
    m.first_month_with_readings,
    m.last_month_with_readings,
    s.ingest_ts
from {{ ref('core__air_monitoring_site') }} s
left join measured m on s.location_id = m.location_id
where s.is_reference_monitor

)

select
    {{ row_sk(['location_id']) }} as row_sk,
    *
from mart
