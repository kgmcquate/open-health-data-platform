-- Mart layer: the presentation table behind the child-welfare trend cube.
-- One row per state x federal fiscal year, states only.
--
-- The National row is dropped here rather than filtered in Cube: a mart is a
-- single, unambiguous grain, and leaving a pre-summed national row in a table
-- whose measures are SUMs is how double-counted KPIs happen. Query
-- core__child_maltreatment_state_year directly if you need NCANDS's own
-- national figure.
{{ config(materialized="table") }}

select
    state,
    fiscal_year,
    victims,
    victim_rate_per_1000,
    fatalities,
    perpetrators,
    children_investigated,
    children_investigated_rate_per_1000,
    -- Share of investigated children who were found to be victims: the one
    -- ratio here that neither source table publishes directly.
    victims / nullif(children_investigated, 0) as victim_substantiation_ratio,
    ingest_ts
from {{ ref('core__child_maltreatment_state_year') }}
where not is_national
