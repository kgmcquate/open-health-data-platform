-- Core layer: Project Tycho Level 1 notifiable-disease surveillance, typed.
--
-- Socrata publishes every column of this dataset as text, so the casts here
-- are the point of the model. `epi_week` arrives as a CDC epi-week key of the
-- form YYYYWW (e.g. 192841 = week 41 of 1928); it is split into year and week
-- and resolved to the Sunday that starts that epi week. try_cast/try_to_*
-- throughout: this is a century-long archive and a handful of rows carry
-- non-numeric placeholders that must not fail the whole build.
{{ config(materialized="table") }}

with source as (
    select * from {{ ref('stg_healthdata_gov__notifiable_disease_tycho') }}
),

parsed as (
    select
        trim(disease)::varchar as disease,
        trim(state)::varchar as state,
        trim(loc)::varchar as location,
        trim(loc_type)::varchar as location_type,
        try_cast(epi_week as int) as epi_week,
        try_cast(left(trim(epi_week), 4) as int) as epi_year,
        try_cast(right(trim(epi_week), 2) as int) as epi_week_of_year,
        try_cast(cases as float) as cases,
        try_cast(incidence_per_100000 as float) as incidence_per_100000
    from source
)

select
    disease,
    state,
    location,
    location_type,
    epi_week,
    epi_year,
    epi_week_of_year,
    -- CDC epi week 1 contains Jan 4; week N therefore starts on the Sunday
    -- (N-1) weeks after the Sunday on or before Jan 4 of that year.
    dateadd(
        week,
        epi_week_of_year - 1,
        date_trunc('week', dateadd(day, 1, date_from_parts(epi_year, 1, 4))) - 1
    )::date as epi_week_start_date,
    cases,
    incidence_per_100000
from parsed
where epi_year is not null
  and epi_week_of_year between 1 and 53
  and disease is not null
