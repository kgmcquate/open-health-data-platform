-- Core layer: vaccination coverage, conformed across the seasonal-influenza
-- and childhood-immunisation tables.
--
-- The two share a shape (geography x season x demographic dimension ->
-- coverage estimate) and differ in two ways, both handled here:
--   * the childhood table has a `dose` column (">=3 Doses", "Primary Series")
--     that the influenza table lacks -- nulled in for flu so the grain matches;
--   * the influenza table has a `month` column, since flu coverage is measured
--     through a season rather than once.
--
-- `coverage_estimate` and the CI bounds are published as text and carry CDC's
-- suppression markers, so every numeric here is try_cast -- a suppressed
-- estimate becomes null rather than failing the build.
--
-- The 95% CI arrives as one string ("41.2 to 45.8"); it is split into numeric
-- bounds so the semantic layer can draw an interval without parsing text.
{{ config(materialized="table") }}

with influenza as (

    select
        'Seasonal influenza'                    as program,
        vaccine,
        cast(null as varchar)                   as dose,
        geography,
        geography_type,
        fips,
        year_season,
        try_cast(month as int)                  as month,
        dimension_type                          as stratification_category,
        dimension                               as stratification,
        coverage_estimate,
        _95_ci                                  as ci_95,
        population_sample_size,
        ingest_ts
    from {{ ref('stg_cdc__flu_vaccination_coverage') }}

), childhood as (

    select
        'Childhood immunisation (0-35 months)'  as program,
        vaccine,
        nullif(dose, '')                        as dose,
        geography,
        geography_type,
        cast(null as varchar)                   as fips,
        year_season,
        cast(null as int)                       as month,
        dimension_type                          as stratification_category,
        dimension                               as stratification,
        coverage_estimate,
        _95_ci                                  as ci_95,
        population_sample_size,
        ingest_ts
    from {{ ref('stg_cdc__child_vaccination_coverage') }}

), unioned as (

    select * from influenza
    union all
    select * from childhood

)

select
    program,
    vaccine,
    dose,
    geography,
    geography_type,
    case geography_type
        when 'HHS Regions/National' then 'national_or_region'
        when 'States/Local Areas'   then 'state'
        when 'Counties'             then 'county'
        else lower(geography_type)
    end                                         as geography_level,
    fips,
    year_season,
    month,
    stratification_category,
    stratification,
    try_cast(coverage_estimate as double)       as coverage_pct,
    try_cast(split_part(ci_95, ' to ', 1) as double) as coverage_ci_low,
    try_cast(split_part(ci_95, ' to ', 2) as double) as coverage_ci_high,
    try_cast(population_sample_size as double)  as sample_size,
    ingest_ts
from unioned
