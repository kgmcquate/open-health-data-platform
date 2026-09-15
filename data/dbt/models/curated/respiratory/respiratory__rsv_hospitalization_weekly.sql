-- Mart layer: RSV-NET weekly hospitalisation rates, overall stratum.
--
-- Grain: state x season x week.
--
-- Filtered hard, and deliberately so: the source multiplexes weekly rates,
-- cumulative rates, monthly rates and clinical-characteristic percentages
-- through one `data_type` column, in two different units. This keeps the
-- observed *weekly rate per 100,000* for the all-ages / all-sex / all-race
-- stratum, which is the series CDC charts. The demographic breakdowns and the
-- clinical characteristics remain in core__rsv_hospitalization_rate_weekly.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    state_abbr,
    season,
    period_end                                  as week_end,
    rate_per_100k                               as hospitalization_rate_per_100k,
    ingest_ts
from {{ ref('core__rsv_hospitalization_rate_weekly') }}
where measure = 'Weekly Rate'
  and rate_type = 'Observed'
  and estimate_type = 'Rate per 100,000'
  and age_category = 'All'
  and sex = 'All'
  and race_ethnicity = 'All'
  and rate_per_100k is not null

)

select
    {{ row_sk(['state_abbr', 'season', 'week_end']) }} as row_sk,
    *
from mart
