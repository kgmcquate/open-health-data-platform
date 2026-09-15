-- Mart layer: weekly measles / H5 wastewater detection rate by state.
--
-- Grain: pathogen x state x week.
--
-- The core fact is one row per PCR sample; the metric people actually want is
-- "what share of samples detected the pathogen this week", so the denominator
-- (samples_tested) is made explicit here rather than left for a cube measure to
-- infer. Both counts are additive, and detection_rate is deliberately *not*
-- pre-divided into a column the cube would then average -- the cube computes it
-- as sum(detections)/sum(samples) so it stays correct at every rollup.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    pathogen,
    state_abbr,
    date_trunc('week', sample_collect_date)::date as week_start,
    count(*)                                    as samples_tested,
    count_if(is_detected)                       as samples_detected,
    count(distinct site_id)                     as sites_reporting,
    sum(case when is_detected then population_served end) as population_with_detection,
    max(ingest_ts)                              as ingest_ts
from {{ ref('core__wastewater_pathogen_detection') }}
where state_abbr is not null
group by 1, 2, 3

)

select
    {{ row_sk(['pathogen', 'state_abbr', 'week_start']) }} as row_sk,
    *
from mart
