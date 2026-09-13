-- Mart layer: the maltreatment mix in each state for the most recent NCANDS
-- federal fiscal year. One row per state x maltreatment type, states only.
--
-- Deliberately NOT denormalised with the state's referral/fatality totals:
-- repeating a state-level total across nine type rows makes every SUM over it
-- wrong by a factor of nine, and a semantic layer cannot express "sum once per
-- state". Those live at their own grain in child_welfare__cps_funnel, which
-- the Cube model joins to on state.
{{ config(materialized="table") }}

select
    state,
    maltreatment_type,
    maltreatment_type_label,
    victims,
    pct_of_victims
from {{ ref('core__child_maltreatment_type_state') }}
where not is_national
