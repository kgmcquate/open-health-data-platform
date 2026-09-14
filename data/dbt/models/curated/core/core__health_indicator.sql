-- Core layer: one conformed long fact for CDC's four "indicator" datasets.
--
-- Chronic Disease Indicators, PLACES, the BRFSS nutrition/activity/obesity
-- table and the Alzheimer's/healthy-aging table are published in four dialects
-- of the same shape: a location, a period, a category + measure, one numeric
-- `data_value` with its own type and unit, a confidence interval, and an
-- optional demographic stratification. Conforming them here is what lets one
-- mart and one cube serve every chronic-disease topic on cdc.gov's front page
-- (diabetes, high blood pressure, healthy weight, smoking, Alzheimer's)
-- instead of four near-identical stacks.
--
-- Column-name mapping is the whole job, and the four disagree on nearly every
-- name -- CDI drops the underscores (`datavalue`, `lowconfidencelimit`),
-- PLACES calls the category `category` and the measure `measure`, the other two
-- call them `class` and `question`. `data_value_type` is preserved rather than
-- filtered: the same measure is published as both crude and age-adjusted
-- prevalence, and choosing between them is a question for the mart, not here.
{{ config(materialized="table") }}

with cdi as (

    -- U.S. Chronic Disease Indicators: state-level, 18 topics, stratified by
    -- race/ethnicity, sex, age or grade (plus an "Overall" stratum).
    select
        'chronic_disease_indicators'                    as source_dataset,
        case when locationabbr = 'US' then 'national' else 'state' end as location_level,
        locationabbr                                    as state_abbr,
        locationdesc                                    as location_desc,
        locationid                                      as location_id,
        try_cast(yearstart as int)                      as year_start,
        try_cast(yearend as int)                        as year_end,
        topic                                           as category,
        question                                        as measure,
        questionid                                      as measure_id,
        try_cast(datavalue as double)                   as data_value,
        datavaluetype                                   as data_value_type,
        datavalueunit                                   as data_value_unit,
        try_cast(lowconfidencelimit as double)          as low_confidence_limit,
        try_cast(highconfidencelimit as double)         as high_confidence_limit,
        coalesce(nullif(stratificationcategory1, ''), 'Overall') as stratification_category,
        coalesce(nullif(stratification1, ''), 'Overall')         as stratification,
        datasource                                      as data_source,
        ingest_ts
    from {{ ref('stg_cdc__chronic_disease_indicators') }}

), places as (

    -- PLACES: county-level model-based estimates. No demographic
    -- stratification at all, so the stratum columns are set to 'Overall' to
    -- keep the grain uniform with the other three.
    select
        'places_county'                                 as source_dataset,
        'county'                                        as location_level,
        stateabbr                                       as state_abbr,
        locationname                                    as location_desc,
        locationid                                      as location_id,
        try_cast(year as int)                           as year_start,
        try_cast(year as int)                           as year_end,
        category                                        as category,
        measure                                         as measure,
        measureid                                       as measure_id,
        try_cast(data_value as double)                  as data_value,
        data_value_type                                 as data_value_type,
        data_value_unit                                 as data_value_unit,
        try_cast(low_confidence_limit as double)        as low_confidence_limit,
        try_cast(high_confidence_limit as double)       as high_confidence_limit,
        'Overall'                                       as stratification_category,
        'Overall'                                       as stratification,
        datasource                                      as data_source,
        ingest_ts
    from {{ ref('stg_cdc__places_county') }}

), nutrition as (

    -- BRFSS nutrition / physical activity / obesity. `class` is the category
    -- ("Obesity / Weight Status", "Physical Activity", "Fruits and Vegetables").
    select
        'nutrition_activity_obesity'                    as source_dataset,
        case when locationabbr = 'US' then 'national' else 'state' end as location_level,
        locationabbr                                    as state_abbr,
        locationdesc                                    as location_desc,
        locationid                                      as location_id,
        try_cast(yearstart as int)                      as year_start,
        try_cast(yearend as int)                        as year_end,
        class                                           as category,
        question                                        as measure,
        questionid                                      as measure_id,
        try_cast(data_value as double)                  as data_value,
        data_value_type                                 as data_value_type,
        data_value_unit                                 as data_value_unit,
        try_cast(low_confidence_limit as double)        as low_confidence_limit,
        try_cast(high_confidence_limit as double)       as high_confidence_limit,
        coalesce(nullif(stratificationcategory1, ''), 'Overall') as stratification_category,
        coalesce(nullif(stratification1, ''), 'Overall')         as stratification,
        datasource                                      as data_source,
        ingest_ts
    from {{ ref('stg_cdc__nutrition_activity_obesity') }}

), alzheimers as (

    -- Alzheimer's / healthy aging. Its confidence limits are published as
    -- *text* (the others are numeric), hence try_cast rather than a plain cast:
    -- footnote placeholders in those columns become null instead of failing
    -- the build.
    select
        'alzheimers_healthy_aging'                      as source_dataset,
        case when locationabbr = 'US' then 'national' else 'state' end as location_level,
        locationabbr                                    as state_abbr,
        locationdesc                                    as location_desc,
        locationid                                      as location_id,
        try_cast(yearstart as int)                      as year_start,
        try_cast(yearend as int)                        as year_end,
        class                                           as category,
        question                                        as measure,
        questionid                                      as measure_id,
        try_cast(data_value as double)                  as data_value,
        data_value_type                                 as data_value_type,
        data_value_unit                                 as data_value_unit,
        try_cast(low_confidence_limit as double)        as low_confidence_limit,
        try_cast(high_confidence_limit as double)       as high_confidence_limit,
        coalesce(nullif(stratificationcategory1, ''), 'Overall') as stratification_category,
        coalesce(nullif(stratification1, ''), 'Overall')         as stratification,
        datasource                                      as data_source,
        ingest_ts
    from {{ ref('stg_cdc__alzheimers_healthy_aging') }}

)

select * from cdi
union all select * from places
union all select * from nutrition
union all select * from alzheimers
