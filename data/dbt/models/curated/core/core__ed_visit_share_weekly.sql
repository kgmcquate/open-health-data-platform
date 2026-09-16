-- Core layer: NSSP emergency-department visit share, reshaped to one row per
-- geography x week x pathogen.
--
-- The source is wide (percent_visits_covid / _influenza / _rsv / _combined,
-- each with a smoothed sibling). Reshaping here is what lets the respiratory
-- mart and its cube treat pathogen as a dimension instead of needing a separate
-- measure per virus. Written as UNION ALL rather than UNPIVOT on purpose:
-- Snowflake's UNPIVOT moves a single column at a time, and each pathogen here
-- has to carry its raw *and* smoothed value together.
--
-- The smoothed columns are matched to their pathogen by CDC's own naming, with
-- one trap: the influenza smoothed column is `percent_visits_smoothed_1`
-- (Socrata's de-duplicated name), while the bare `percent_visits_smoothed` is
-- the *combined* series, not influenza's.
--
-- `trend_source` distinguishes three nested geography levels shipped in one
-- table (United States / State / HSA). They overlap, so anything aggregating
-- across rows must filter to one level -- hence geography_level is carried
-- forward rather than collapsed.
{{ config(materialized="table") }}

with base as (

    select
        geography,
        case trend_source
            when 'United States' then 'national'
            when 'State'         then 'state'
            when 'HSA'           then 'hsa'
            else lower(trend_source)
        end                                     as geography_level,
        county,
        hsa                                     as health_service_area,
        cast(fips as varchar)                   as fips,
        -- week_end arrives as a timestamp (Socrata calendar_date -> dlt timestamp).
        cast(week_end as date)                  as week_end,
        percent_visits_covid,
        percent_visits_influenza,
        percent_visits_rsv,
        percent_visits_combined,
        percent_visits_smoothed_covid,
        percent_visits_smoothed_1               as percent_visits_smoothed_influenza,
        percent_visits_smoothed_rsv,
        percent_visits_smoothed                 as percent_visits_smoothed_combined,
        ingest_ts
    from {{ ref('stg_cdc__ed_visit_trajectories') }}
    where week_end is not null

), reshaped as (

    {% set pathogens = [
        ('COVID-19',  'percent_visits_covid',     'percent_visits_smoothed_covid'),
        ('Influenza', 'percent_visits_influenza', 'percent_visits_smoothed_influenza'),
        ('RSV',       'percent_visits_rsv',       'percent_visits_smoothed_rsv'),
        ('Combined',  'percent_visits_combined',  'percent_visits_smoothed_combined'),
    ] %}
    {% for label, raw_col, smoothed_col in pathogens %}
    select
        geography,
        geography_level,
        county,
        health_service_area,
        fips,
        week_end,
        '{{ label }}'                           as pathogen,
        try_cast({{ raw_col }} as double)       as percent_of_ed_visits,
        try_cast({{ smoothed_col }} as double)  as percent_of_ed_visits_smoothed,
        ingest_ts
    from base
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

)

select * from reshaped
