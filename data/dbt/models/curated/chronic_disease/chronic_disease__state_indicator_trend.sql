-- Mart layer: state-level chronic-disease indicator trend, the presentation
-- table behind the chronic_disease cube.
--
-- Grain: source_dataset x state x year x measure x data_value_type x stratum x
-- stratum_2 x response. The last two are null for every source except
-- Alzheimer's/healthy-aging (cross-tabulated by two strata at once) and CDI
-- (categorical questions with more than one answer option), respectively --
-- see core__health_indicator.sql for why they can't be folded into
-- `stratification`.
-- PLACES is excluded here because it is county-grain -- it gets its own mart
-- (chronic_disease__county_prevalence) rather than being averaged up to a state
-- number it was never modelled to support.
--
-- data_value_type is deliberately a dimension, not a filter: CDC publishes the
-- same measure as both crude and age-adjusted prevalence, and averaging the two
-- together is meaningless. The cube defines a separate measure per type rather
-- than one AVG over the column (semantic/cube/model/chronic_disease.yml).
--
-- The stratum is kept rather than filtered to "Overall" because the
-- Alzheimer's source has *no* overall row -- every one of its rows is stratified
-- by age group -- so an Overall-only mart would silently drop that dataset
-- entirely.
{{ config(materialized="table") }}

select
    source_dataset,
    location_level,
    state_abbr,
    location_desc                               as state_name,
    year_start                                  as year,
    category,
    measure,
    measure_id,
    data_value_type,
    data_value_unit,
    stratification_category,
    stratification,
    stratification_category_2,
    stratification_2,
    data_value,
    low_confidence_limit,
    high_confidence_limit,
    high_confidence_limit - low_confidence_limit as confidence_interval_width,
    ingest_ts
from {{ ref('core__health_indicator') }}
where location_level in ('state', 'national')
  and data_value is not null
