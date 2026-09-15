-- Mart layer: state-week share of emergency department visits by respiratory
-- pathogen -- NSSP's headline "how busy are EDs with COVID/flu/RSV" series.
--
-- Grain: state x week x pathogen. Filtered to geography_level = 'state' because
-- the source nests national, state and health-service-area rows in one table;
-- mixing levels would double-count. The HSA detail stays in core.
--
-- The values are percentages of all ED visits, so they are averaged, never
-- summed. Note the four pathogen values are not mutually exclusive: 'Combined'
-- is CDC's own union of the other three, so summing across pathogen
-- double-counts -- the cube filters to a single pathogen per measure.
{{ config(materialized="table") }}

select
    geography                                   as state,
    week_end,
    pathogen,
    percent_of_ed_visits,
    percent_of_ed_visits_smoothed,
    ingest_ts
from {{ ref('core__ed_visit_share_weekly') }}
where geography_level = 'state'
