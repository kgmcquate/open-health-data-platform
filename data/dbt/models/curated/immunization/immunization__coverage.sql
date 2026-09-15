-- Mart layer: vaccination coverage, seasonal influenza + childhood
-- immunisation in one table.
--
-- Grain: program x vaccine x dose x geography x fips x season x month x
-- stratum. `fips` has to be in the grain, not just `geography`: the flu table
-- includes county-level rows, and county names repeat across states (there
-- are ~30 Washington Countys), so `geography` alone collapses distinct
-- counties onto the same key -- this is what tripped the uniqueness test.
--
-- Coverage is a percentage of a population, so it is averaged, never summed,
-- and averaging across geographies is unweighted (CDC publishes no consistent
-- denominator here) -- fine for comparing states, wrong for computing a
-- national figure. The national/HHS-region rows CDC publishes are the correct
-- source for that, which is why geography_level is kept as a dimension.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    program,
    vaccine,
    dose,
    geography,
    geography_level,
    geography_type,
    fips,
    year_season,
    month,
    stratification_category,
    stratification,
    coverage_pct,
    coverage_ci_low,
    coverage_ci_high,
    coverage_ci_high - coverage_ci_low          as coverage_ci_width,
    sample_size,
    ingest_ts
from {{ ref('core__vaccination_coverage') }}
where coverage_pct is not null

)

select
    {{ row_sk([
        'program', 'vaccine', 'dose', 'geography',
        'geography_level', 'fips', 'year_season', 'month',
        'stratification_category', 'stratification',
    ]) }} as row_sk,
    *
from mart
