-- Mart layer: vaccination coverage, seasonal influenza + childhood
-- immunisation in one table.
--
-- Grain: program x vaccine x dose x geography x season x month x stratum.
--
-- Coverage is a percentage of a population, so it is averaged, never summed,
-- and averaging across geographies is unweighted (CDC publishes no consistent
-- denominator here) -- fine for comparing states, wrong for computing a
-- national figure. The national/HHS-region rows CDC publishes are the correct
-- source for that, which is why geography_level is kept as a dimension.
{{ config(materialized="table") }}

select
    program,
    vaccine,
    dose,
    geography,
    geography_level,
    geography_type,
    year_season,
    month,
    stratification_category,
    stratification,
    coverage_pct,
    coverage_ci_low,
    coverage_ci_high,
    coverage_ci_high - coverage_ci_low          as coverage_ci_width,
    sample_size
from {{ ref('core__vaccination_coverage') }}
where coverage_pct is not null
