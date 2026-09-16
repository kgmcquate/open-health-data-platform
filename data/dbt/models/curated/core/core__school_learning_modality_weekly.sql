-- Core layer: the two school-year learning-modality files conformed into one
-- weekly district series.
--
-- They are published as separate datasets with identical schemas; unioning
-- here (rather than in each mart) means `school_year` is the only thing a
-- consumer needs to filter on, and a future year's file is a third branch in
-- this model alone. `learning_modality` is normalised to initial caps because
-- the two files disagree on casing for the same three values.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

-- Columns are listed explicitly rather than `select *`: UNION ALL matches by
-- POSITION, and the two source tables' physical column order is whatever dlt
-- inferred on first load -- the two defs.yaml files already advertise them in
-- different orders. A `*` here would silently transpose columns.
{% set cols = [
    'district_nces_id', 'district_name', 'city', 'state', 'zip_code',
    'week', 'learning_modality', 'student_count', 'operational_schools',
    'ingest_ts',
] %}

with unioned as (
    {% for year in ['2020_2021', '2021_2022'] %}
    select
        '{{ year | replace('_', '-') }}' as school_year,
        {{ cols | join(',\n        ') }}
    from {{ ref('stg_healthdata_gov__school_learning_modalities_' ~ year) }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    school_year::varchar as school_year,
    district_nces_id::varchar as district_nces_id,
    district_name::varchar as district_name,
    city::varchar as city,
    upper(trim(state))::varchar as state,
    zip_code::varchar as zip_code,
    cast(week as date) as week,
    {{ initcap('trim(learning_modality)') }}::varchar as learning_modality,
    student_count::int as student_count,
    operational_schools::int as operational_schools,
    ingest_ts
from unioned
where district_nces_id is not null
  and week is not null

)

select
    {{ row_sk(['district_nces_id', 'week']) }} as row_sk,
    *
from mart
