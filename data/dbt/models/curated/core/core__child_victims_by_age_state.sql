-- Core layer: NCANDS victims by single year of age, pivoted from wide (one
-- column pair per age) to long (one row per state x age group).
--
-- Socrata's column names here are the age itself, which collides: `_1` is age
-- 1 while `_1_1` is the collision-renamed "younger than 1" count, whose rate
-- is published as `_less_than_1_rate_per_1000_children`. `age_group` is a
-- string, not an int, because "<1" and "unborn" are real categories that no
-- integer age can represent; `age_years` carries the numeric age where one
-- exists and is null otherwise, for ordering and banding.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

{% set ages = range(1, 18) | list %}

with source as (
    select * from {{ ref('stg_healthdata_gov__child_victims_by_age') }}
),

unpivoted as (
    select
        state,
        'unborn' as age_group,
        null::int as age_years,
        _unborn::int as victims,
        null::float as rate_per_1000_children,
        _total_victims::int as total_victims,
        ingest_ts
    from source

    union all

    select
        state,
        '<1' as age_group,
        0::int as age_years,
        _1_1::int as victims,
        _less_than_1_rate_per_1000_children::float as rate_per_1000_children,
        _total_victims::int as total_victims,
        ingest_ts
    from source

    {% for age in ages %}
    union all

    select
        state,
        '{{ age }}' as age_group,
        {{ age }}::int as age_years,
        _{{ age }}::int as victims,
        _{{ age }}_rate_per_1000_children::float as rate_per_1000_children,
        _total_victims::int as total_victims,
        ingest_ts
    from source
    {% endfor %}
)

select
    state::varchar as state,
    lower(state) = 'national' as is_national,
    age_group::varchar as age_group,
    age_years,
    victims,
    rate_per_1000_children,
    total_victims,
    ingest_ts
from unpivoted
where state is not null

)

select
    {{ row_sk(['state', 'age_group']) }} as row_sk,
    *
from mart
