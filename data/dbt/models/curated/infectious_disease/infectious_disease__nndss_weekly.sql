-- Mart layer: NNDSS weekly notifiable-disease counts, jurisdictions only.
--
-- Grain: disease x reporting area x MMWR year x MMWR week.
--
-- Filtered to is_jurisdiction so the census-division rollups and the national
-- total that NNDSS ships as sibling rows can't be summed together with the
-- states -- that is the single easiest way to get a wrong number out of this
-- dataset. The national series is still available from core, unfiltered.
--
-- `current_week_cases` is the weekly count and is additive across diseases and
-- areas. `cumulative_ytd_cases` is NOT additive across weeks (it already
-- accumulates), which is why the cube exposes it only as a max.
{{ config(materialized="table") }}

select
    disease,
    reporting_area,
    mmwr_year,
    mmwr_week,
    current_week_cases,
    previous_52_week_max,
    cumulative_ytd_cases,
    cumulative_ytd_cases_prior_year,
    -- Year-over-year change on the cumulative figure, CDC's own headline
    -- comparison for a notifiable disease.
    cumulative_ytd_cases - cumulative_ytd_cases_prior_year as cumulative_ytd_change,
    current_week_flag,
    cumulative_ytd_flag,
    ingest_ts
from {{ ref('core__nndss_weekly_case_counts') }}
where is_jurisdiction
