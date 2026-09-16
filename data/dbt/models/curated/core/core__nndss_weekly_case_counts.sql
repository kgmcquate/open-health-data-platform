-- Core layer: NNDSS weekly provisional case counts, typed.
--
-- NNDSS publishes four measures per disease x reporting area x MMWR week under
-- opaque names; the meanings below are CDC's own column labels from the
-- dataset's metadata (api/views/x9gk-5huc.json), not a guess:
--   m1 = current week      m2 = previous 52-week maximum
--   m3 = cumulative YTD, current MMWR year
--   m4 = cumulative YTD, previous MMWR year
-- The `*_flag` siblings carry CDC's suppression/footnote markers and are kept
-- so a null count can be told apart from a suppressed one.
--
-- `states` is the reporting area and mixes real jurisdictions with census
-- rollups ("EAST NORTH CENTRAL") and a national total, so is_aggregate flags
-- the non-jurisdiction rows rather than dropping them -- summing across
-- reporting areas without that filter double-counts.
{{ config(materialized="table") }}

select
    label                                       as disease,
    {{ initcap('states') }}                     as reporting_area,
    states                                      as reporting_area_raw,
    try_cast(year as int)                       as mmwr_year,
    try_cast(week as int)                       as mmwr_week,
    -- MMWR weeks are ISO-like but CDC-defined; this is the week number, not a
    -- date. A calendar date would need the MMWR calendar, which CDC does not
    -- publish in this table.
    try_cast(m1 as int)                         as current_week_cases,
    try_cast(m2 as int)                         as previous_52_week_max,
    try_cast(m3 as int)                         as cumulative_ytd_cases,
    try_cast(m4 as int)                         as cumulative_ytd_cases_prior_year,
    m1_flag                                     as current_week_flag,
    m3_flag                                     as cumulative_ytd_flag,
    states not in (
        'UNITED STATES', 'NEW ENGLAND', 'MIDDLE ATLANTIC', 'EAST NORTH CENTRAL',
        'WEST NORTH CENTRAL', 'SOUTH ATLANTIC', 'EAST SOUTH CENTRAL',
        'WEST SOUTH CENTRAL', 'MOUNTAIN', 'PACIFIC', 'US RESIDENTS',
        'NON-US RESIDENTS', 'TOTAL'
    )                                           as is_jurisdiction,
    ingest_ts
from {{ ref('stg_cdc__nndss_weekly') }}
where label is not null
