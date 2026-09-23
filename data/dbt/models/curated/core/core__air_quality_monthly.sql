-- Core layer: monthly pollutant readings from OpenAQ's reference-monitor
-- network, typed and joined to the station that produced them. One row per
-- sensor x month -- a sensor measures exactly one parameter at one station,
-- so that is also one row per (station, pollutant, month).
--
-- **Never aggregate across parameter without grouping on it.** pm25 is
-- µg/m3, o3 and no2 are ppm; an average over a mixed selection is a number
-- with no unit and no meaning. The unit travels with every row
-- (`parameter_units`) so a consumer can see what it is holding, and the
-- marts and cubes downstream expose per-pollutant measures rather than one
-- "average reading" measure for exactly this reason.
--
-- month_start is derived from the period bounds, through
-- macros/openaq_period_month.sql -- the same expression the raw rows are
-- deduped on in stg_openaq__monthly_measurements, so the month here and the
-- month the dedupe keys on cannot disagree. It is deliberately NOT derived
-- from `period_label`: OpenAQ's `period.label` is the interval ("1 month"),
-- not a year-month, and reading it as one nulled month_start on every row --
-- which, with `where month_start is not null` downstream, left
-- environmental_health__air_quality_monthly empty. The raw label column is
-- not carried forward here for the same reason.
--
-- The coverage columns are the honest caveat on every average here. A
-- month's mean is computed from however many hourly values the sensor
-- actually reported, which can be a handful: `pct_complete` is
-- observed/expected, and `is_complete_month` (>= 75%) is the cut the marts
-- use as a default quality filter. 75% is EPA's own completeness threshold
-- for treating a period's average as valid, and is the least arbitrary line
-- available -- OpenAQ publishes no quality flag of its own beyond
-- `flaginfo__hasflags`, which marks a known-affected period (maintenance and
-- similar) rather than sparseness.
--
-- Station attributes are denormalised onto the fact rather than left to a
-- join. Cube can join cubes, but every one of these dimensions is something
-- a query slices readings by (state-level maps key off latitude/longitude,
-- provider comparisons off provider_name), and a join per query buys
-- nothing here -- the station table is ~1,200 rows against a fact that grows
-- by one row per sensor per month.
{{ config(materialized="table") }}

select
    try_cast(m.sensor_id as bigint)                          as sensor_id,
    try_cast(m.location_id as bigint)                        as location_id,
    {{ openaq_period_month('m.period__datetimefrom__utc', 'm.period__datetimeto__utc') }} as month_start,
    m.parameter__name                                    as parameter,
    m.parameter__units                                   as parameter_units,

    -- Station attributes, from the locations snapshot.
    coalesce(s.location_name, m.location_name)           as location_name,
    s.locality,
    s.timezone,
    s.latitude,
    s.longitude,
    s.provider_name,
    s.owner_name,
    coalesce(s.country_code, m.country)                  as country_code,
    s.country_name,
    s.is_mobile,
    s.is_reference_monitor,

    -- The month's distribution, all in `parameter_units`.
    try_cast(m.summary__avg as double)                   as avg_value,
    try_cast(m.summary__median as double)                as median_value,
    try_cast(m.summary__min as double)                   as min_value,
    try_cast(m.summary__max as double)                   as max_value,
    try_cast(m.summary__q25 as double)                   as p25_value,
    try_cast(m.summary__q75 as double)                   as p75_value,
    try_cast(m.summary__q98 as double)                   as p98_value,
    try_cast(m.summary__sd as double)                    as stddev_value,

    -- How much of the month the average actually rests on.
    try_cast(m.coverage__observedcount as double)        as observed_hours,
    try_cast(m.coverage__expectedcount as double)        as expected_hours,
    try_cast(m.coverage__percentcomplete as double)      as pct_complete,
    try_cast(m.coverage__percentcomplete as double) >= 75 as is_complete_month,
    try_cast(m.flaginfo__hasflags as boolean)                as has_data_flags,
    try_cast(m.coverage__datetimefrom__utc as timestamp) as observed_from_utc,
    try_cast(m.coverage__datetimeto__utc as timestamp)   as observed_to_utc,

    m.ingest_ts
from {{ ref('stg_openaq__monthly_measurements') }} m
left join {{ ref('core__air_monitoring_site') }} s
    on try_cast(m.location_id as bigint) = s.location_id
