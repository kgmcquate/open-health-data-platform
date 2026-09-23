-- Core layer: the monitoring-station dimension behind every OpenAQ fact. One
-- row per OpenAQ location id.
--
-- Kept as its own model rather than folded into
-- core__air_quality_monthly: the locations walk indexes every station OpenAQ
-- knows about worldwide, while the monthly fan-out is bounded to US
-- reference monitors by `max_locations`/`max_sensors` (see
-- ohdp_orchestration/defs/openaq/datasets/defs.yaml for why those caps
-- exist). So this table is deliberately a superset of the stations that have
-- measurements -- "which stations exist" and "which stations we have readings
-- for" are different questions, and collapsing them would silently answer the
-- second when asked the first.
--
-- ismobile / ismonitor are the two quality caveats worth carrying forward:
-- a mobile platform's coordinates are only its first measured point, not a
-- standing location, and ismonitor separates government reference-grade
-- instruments from low-cost sensor nodes. The monthly ingestion filters to
-- ismonitor = true, so every station with readings is a reference monitor;
-- this table still holds the rest.
{{ config(materialized="table") }}

select
    try_cast(id as bigint)                          as location_id,
    name                                        as location_name,
    locality,
    timezone,
    try_cast(ismobile as boolean)                   as is_mobile,
    try_cast(ismonitor as boolean)                  as is_reference_monitor,
    try_cast(coordinates__latitude as double)   as latitude,
    try_cast(coordinates__longitude as double)  as longitude,
    try_cast(provider__id as bigint)                as provider_id,
    provider__name                              as provider_name,
    try_cast(owner__id as bigint)                   as owner_id,
    owner__name                                 as owner_name,
    try_cast(country__id as bigint)                 as country_id,
    country__code                               as country_code,
    country__name                               as country_name,
    try_cast(datetimefirst__utc as timestamp)   as first_measurement_at_utc,
    try_cast(datetimelast__utc as timestamp)    as last_measurement_at_utc,
    ingest_ts
from {{ ref('stg_openaq__locations') }}
