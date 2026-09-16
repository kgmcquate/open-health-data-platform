-- Core layer: NCANDS perpetrators, pivoted from wide (one column per
-- relationship) to long (one row per state x relationship to victim).
--
-- Unique counts: a perpetrator appears exactly once, and anyone with two or
-- more relationships lands in `multiple_relationships` rather than in each
-- individual category -- so these rows DO sum to total_perpetrators.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

{% set relationships = [
    ('parent',                      'Parent'),
    ('unmarried_partner_of_parent', 'Unmarried partner of parent'),
    ('relative',                    'Relative (non-parental family)'),
    ('foster_parent',               'Foster parent'),
    ('legal_guardian',              'Legal guardian'),
    ('group_home_and_residential',  'Group home or residential staff'),
    ('child_daycare_provider',      'Child daycare provider'),
    ('friend_and_neighbor',         'Friend or neighbor'),
    ('other_professional',          'Other professional'),
    ('multiple_relationships',      'Multiple relationships'),
    ('other',                       'Other'),
    ('unknown',                     'Unknown'),
] %}

with source as (
    select * from {{ ref('stg_healthdata_gov__perpetrators_by_relationship') }}
),

unpivoted as (
    {% for code, label in relationships %}
    select
        state,
        '{{ code }}' as relationship,
        '{{ label }}' as relationship_label,
        {{ code }}::int as perpetrators,
        total_perpetrators::int as total_perpetrators,
        ingest_ts
    from source
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    state::varchar as state,
    lower(state) = 'national' as is_national,
    relationship::varchar as relationship,
    relationship_label::varchar as relationship_label,
    perpetrators,
    total_perpetrators,
    ingest_ts
from unpivoted
where state is not null

)

select
    {{ row_sk(['state', 'relationship']) }} as row_sk,
    *
from mart
