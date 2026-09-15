-- Core layer: NCANDS maltreatment types, pivoted from wide (one column pair per
-- type) to long (one row per state x maltreatment type).
--
-- These are DUPLICATE counts: a victim is counted once per maltreatment
-- category they were substantiated for, so the type rows sum to MORE than
-- total_victims. `pct_of_victims` is the source's own percentage, computed
-- against the unique victim count -- do not re-derive it from `victims`.
{{ config(materialized="table") }}

{% set types = [
    ('medical_neglect_only',        'Medical neglect',            'medical_neglect_only',        'medical_neglect_only_percent'),
    ('neglect_only',                'Neglect',                    'neglect_only',                'neglect_only_percent'),
    ('physical_abuse_only',         'Physical abuse',             'physical_abuse_only',         'physical_abuse_only_percent'),
    ('sexual_abuse_only',           'Sexual abuse',               'sexual_abuse_only',           'sexual_abuse_only_percent'),
    ('sex_trafficking_only',        'Sex trafficking',            'sex_trafficking_only',        'sex_trafficking_only_percent'),
    ('psychological_maltreatment',  'Psychological maltreatment', 'psychological_maltreatment',  'psychological_maltreatment_1'),
    ('multiple_maltreatment_types', 'Multiple types',             'multiple_maltreatment_types', 'multiple_maltreatment_types_1'),
    ('other_only',                  'Other',                      'other_only',                  'other_only_percent'),
    ('unknown_only',                'Unknown',                    'unknown_only',                'unknown_only_percent'),
] %}

-- The two `_1`-suffixed percent columns above are Socrata's collision-renamed
-- percentage fields (psychological_maltreatment / multiple_maltreatment_types
-- each publish a count and a percent under near-identical labels).

with source as (
    select * from {{ ref('stg_healthdata_gov__maltreatment_types_of_victims') }}
),

unpivoted as (
    {% for code, label, count_col, pct_col in types %}
    select
        state,
        '{{ code }}' as maltreatment_type,
        '{{ label }}' as maltreatment_type_label,
        {{ count_col }}::int as victims,
        {{ pct_col }}::float as pct_of_victims,
        total_victims::int as total_victims,
        ingest_ts
    from source
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)

select
    state::varchar as state,
    lower(state) = 'national' as is_national,
    maltreatment_type::varchar as maltreatment_type,
    maltreatment_type_label::varchar as maltreatment_type_label,
    victims,
    pct_of_victims,
    total_victims,
    ingest_ts
from unpivoted
where state is not null
