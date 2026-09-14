-- Core layer: NSSP mental health-related ED visit rates, one row per month x
-- condition x demographic stratum.
--
-- `month_end` is published as a bare 'YYYY-MM' string, so it is completed to
-- the first of the month before casting -- the value is a month, and a date is
-- what the semantic layer can bucket on.
--
-- The demographic columns are a single stratum pair (type + value) rather than
-- one column per dimension, and include a 'Total' type whose value is 'All'.
-- That total row is *not* dropped: it is the correct series to chart on its
-- own, and is_total exists so an aggregate can exclude it instead of
-- double-counting.
{{ config(materialized="table") }}

select
    condition,
    {{ try_to_date("cast(month_end as varchar) || '-01'") }}             as month_start,
    last_day({{ try_to_date("cast(month_end as varchar) || '-01'") }})   as month_end,
    demographics_type                           as stratification_category,
    demographics_values                         as stratification,
    demographics_type = 'Total'                 as is_total,
    try_cast(rate_per_100000_visits as double)  as rate_per_100k_visits,
    ingest_ts
from {{ ref('stg_cdc__ed_visits_mental_health') }}
where {{ try_to_date("cast(month_end as varchar) || '-01'") }} is not null
