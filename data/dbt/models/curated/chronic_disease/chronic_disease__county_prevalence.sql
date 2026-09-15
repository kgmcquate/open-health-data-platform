-- Mart layer: PLACES county-level prevalence, latest release year only.
--
-- Grain: county x measure x data_value_type. PLACES ships two release years in
-- one table (2022 and 2023 in the 2025 release); this keeps the most recent per
-- measure so a map does not plot two vintages on top of each other. The older
-- rows stay in core__health_indicator for anyone doing a trend.
--
-- Every PLACES value is a modelled percentage with a CI, so `data_value` is a
-- prevalence to be averaged or mapped -- never summed across counties.
{{ config(materialized="table") }}

with places as (

    select *
    from {{ ref('core__health_indicator') }}
    where source_dataset = 'places_county'
      and data_value is not null

), latest_year as (

    select measure_id, max(year_start) as year
    from places
    group by 1

)

select
    p.state_abbr,
    p.location_desc                             as county_name,
    p.location_id                               as county_fips,
    p.year_start                                as year,
    p.category,
    p.measure,
    p.measure_id,
    p.data_value_type,
    p.data_value                                as prevalence_pct,
    p.low_confidence_limit                      as prevalence_ci_low,
    p.high_confidence_limit                     as prevalence_ci_high,
    p.ingest_ts
from places p
join latest_year ly
  on p.measure_id = ly.measure_id
 and p.year_start = ly.year
