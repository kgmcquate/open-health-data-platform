-- Core layer: the NCANDS five-year trend tables, conformed to one row per
-- state x federal fiscal year.
--
-- Each source is published WIDE -- one column per year (`_2015` .. `_2019`),
-- and for two of them a matching rate column whose name differs between
-- publications (`_rate_per_1000_children` vs `_rate_per_1_000_children`). The
-- loop below unpivots each to long form, then the four are full-outer-joined
-- on (state, fiscal_year) so a state missing from one submission still keeps
-- the measures it did report.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

{% set years = [2015, 2016, 2017, 2018, 2019] %}

with victims as (
    {% for y in years %}
    select
        state,
        {{ y }} as fiscal_year,
        _{{ y }}::int as victims,
        _{{ y }}_rate_per_1000_children::float as victim_rate_per_1000,
        ingest_ts
    from {{ ref('stg_healthdata_gov__child_victims_trend') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

fatalities as (
    {% for y in years %}
    select state, {{ y }} as fiscal_year, _{{ y }}::int as fatalities, ingest_ts
    from {{ ref('stg_healthdata_gov__child_fatalities_trend') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

perpetrators as (
    {% for y in years %}
    select state, {{ y }} as fiscal_year, _{{ y }}::int as perpetrators, ingest_ts
    from {{ ref('stg_healthdata_gov__perpetrators_trend') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

investigated as (
    {% for y in years %}
    select
        state,
        {{ y }} as fiscal_year,
        _{{ y }}::int as children_investigated,
        _{{ y }}_rate_per_1_000_children::float as children_investigated_rate_per_1000,
        ingest_ts
    from {{ ref('stg_healthdata_gov__children_investigated_trend') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

-- A spine of every (state, fiscal_year) seen in any of the four sources, then
-- a LEFT JOIN per source. Chaining `full outer join` off the first CTE instead
-- would drop rows: once `v.state` is null for a state that source never
-- reported, `v.state = p.state` can never match, so that state's perpetrator
-- count silently disappears.
spine as (
    select state, fiscal_year from victims
    union
    select state, fiscal_year from fatalities
    union
    select state, fiscal_year from perpetrators
    union
    select state, fiscal_year from investigated
)

select
    s.state::varchar as state,
    s.fiscal_year::int as fiscal_year,
    -- NCANDS publishes a "National" row alongside the states; it is a sum over
    -- reporting states only, so every consumer has to opt in or out explicitly
    -- rather than silently double-counting it in a state-level aggregate.
    lower(s.state) = 'national' as is_national,
    v.victims,
    v.victim_rate_per_1000,
    f.fatalities,
    p.perpetrators,
    i.children_investigated,
    i.children_investigated_rate_per_1000,
    greatest(v.ingest_ts, f.ingest_ts, p.ingest_ts, i.ingest_ts) as ingest_ts
from spine s
left join victims v on s.state = v.state and s.fiscal_year = v.fiscal_year
left join fatalities f on s.state = f.state and s.fiscal_year = f.fiscal_year
left join perpetrators p on s.state = p.state and s.fiscal_year = p.fiscal_year
left join investigated i on s.state = i.state and s.fiscal_year = i.fiscal_year
where s.state is not null

)

select
    {{ row_sk(['state', 'fiscal_year']) }} as row_sk,
    *
from mart
