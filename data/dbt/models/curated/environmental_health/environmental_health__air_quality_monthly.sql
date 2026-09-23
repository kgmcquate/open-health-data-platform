-- Mart layer: monthly pollutant concentrations at US reference monitors, the
-- presentation table behind the environmental_health cube.
--
-- Grain: sensor_id x month_start. One sensor measures one pollutant at one
-- station, so this is equivalently station x pollutant x month.
--
-- **Units differ by pollutant** (pm25 in µg/m3, o3 and no2 in ppm), so
-- nothing here may be averaged or summed across `parameter` -- the cube
-- exposes one measure per pollutant rather than a single "average reading"
-- for that reason, and parameter_units is carried on every row so the number
-- is never seen without its unit.
--
-- Sparse months are kept, not filtered out. A month's average can rest on a
-- handful of hourly readings; `pct_complete` and `is_complete_month` (>= 75%
-- of expected hours, EPA's own completeness threshold) are the columns to
-- filter on, and the cube's headline measures apply that filter themselves.
-- Dropping the rows here instead would make "this monitor reported almost
-- nothing this month" indistinguishable from "this monitor does not exist",
-- which is the more useful of the two facts about a sparse station.
--
-- Scope, and it is narrower than "US air quality": the monthly fan-out is
-- capped at 400 locations and 800 sensors per country and restricted to
-- government reference monitors and three pollutants (pm25, o3, no2) -- see
-- ohdp_orchestration/defs/openaq/datasets/defs.yaml for why those caps are
-- load-bearing. Treat this as a consistent panel of stations (they are taken
-- in id order, so it is the same panel month over month), not a census.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    sensor_id,
    location_id,
    location_name,
    month_start,
    parameter,
    parameter_units,
    locality,
    country_code,
    latitude,
    longitude,
    provider_name,
    owner_name,
    timezone,
    avg_value,
    median_value,
    min_value,
    max_value,
    p25_value,
    p75_value,
    p98_value,
    stddev_value,
    observed_hours,
    expected_hours,
    pct_complete,
    is_complete_month,
    has_data_flags,
    ingest_ts
from {{ ref('core__air_quality_monthly') }}
where month_start is not null

)

select
    {{ row_sk(['sensor_id', 'month_start']) }} as row_sk,
    *
from mart
