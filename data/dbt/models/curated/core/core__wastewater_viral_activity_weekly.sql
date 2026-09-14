-- Core layer: NWSS wastewater viral activity level (WVAL), one row per
-- sewershed site x week x pathogen.
--
-- This is the cross-pathogen feed -- SARS-CoV-2, Influenza A and RSV are rows,
-- not columns -- which is why it is the one the marts lead with. `site_wval` is
-- a normalised activity level, not a concentration, so it is averaged rather
-- than summed downstream; `site_wval_category` is CDC's own banding of it
-- (Very Low / Low / Moderate / High / Very High).
{{ config(materialized="table") }}

select
    site                                        as site_id,
    state_territory                             as state_abbr,
    counties_served,
    try_cast(population_served as bigint)       as population_served,
    pathogen_target                             as pathogen,
    try_to_date(week_end)                       as week_end,
    try_cast(site_wval as double)               as viral_activity_level,
    site_wval_category                          as activity_category,
    -- Ordinal form of the category, so a dashboard can sort or threshold on it
    -- without hard-coding CDC's band names in five places.
    case site_wval_category
        when 'Very Low' then 1
        when 'Low'      then 2
        when 'Moderate' then 3
        when 'High'     then 4
        when 'Very High' then 5
    end                                         as activity_rank,
    source                                      as reporting_source,
    ingest_ts
from {{ ref('stg_cdc__wastewater_viral_activity') }}
where try_to_date(week_end) is not null
