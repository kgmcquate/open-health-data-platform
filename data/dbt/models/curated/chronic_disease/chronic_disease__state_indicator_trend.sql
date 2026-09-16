-- Mart layer: state-level chronic-disease indicator trend, the presentation
-- table behind the chronic_disease cube.
--
-- Grain: source_dataset x state x reporting window (year + year_end) x measure
-- x data_value_type x stratum x stratum_2. stratum_2 is null for every source
-- except Alzheimer's/healthy-aging, which cross-tabulates by two strata at
-- once -- see core__health_indicator.sql for why it can't be folded into
-- `stratification`.
--
-- year_end is in the grain because CDC reports the same start year over more
-- than one window. The Alzheimer's source publishes both a single-year
-- estimate and a multi-year pooled one for the same measure, state and
-- stratum: 2019/2019 alongside 2019/2022, and 2021/2021 alongside 2021/2022.
-- They are different statistics, not a republication -- Connecticut's Q30 for
-- women aged 50-64 is 7.5% over 2019 and 14.3% over 2019-2022 -- so keying on
-- the start year alone collapsed two real observations into one and made this
-- mart's grain a lie. Note the analytical edge this leaves: a query that
-- groups by `year` without constraining `year_end` averages single-year and
-- pooled estimates together, the same trap data_value_type sets below.
--
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

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    source_dataset,
    location_level,
    state_abbr,
    location_desc                               as state_name,
    year_start                                  as year,
    year_end,
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

)

select
    {{ row_sk([
        'source_dataset', 'state_abbr', 'year', 'year_end',
        'measure_id', 'data_value_type', 'stratification_category',
        'stratification', 'stratification_category_2',
        'stratification_2',
    ]) }} as row_sk,
    *
from mart
