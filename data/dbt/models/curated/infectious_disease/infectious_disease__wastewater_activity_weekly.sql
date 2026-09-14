-- Mart layer: state-week wastewater viral activity, rolled up from sewershed
-- sites.
--
-- Grain: state x week x pathogen.
--
-- The site-level activity level is a normalised index, so the state figure is a
-- population-weighted mean of its sites rather than a sum -- a state with more
-- monitored sewersheds does not have "more" viral activity. The unweighted mean
-- is kept alongside it so the two can be compared when coverage is thin.
{{ config(materialized="table") }}

select
    state_abbr,
    pathogen,
    week_end,
    count(distinct site_id)                     as sites_reporting,
    sum(population_served)                      as population_covered,
    avg(viral_activity_level)                   as avg_activity_level,
    {{ div0('sum(viral_activity_level * population_served)',
            'sum(population_served)') }}        as pop_weighted_activity_level,
    avg(activity_rank)                          as avg_activity_rank,
    max(activity_rank)                          as max_activity_rank
from {{ ref('core__wastewater_viral_activity_weekly') }}
where state_abbr is not null
group by 1, 2, 3
