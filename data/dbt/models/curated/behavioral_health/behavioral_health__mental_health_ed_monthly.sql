-- Mart layer: mental health-related ED visit rates by month and condition.
--
-- Grain: month x condition x demographic stratum.
--
-- The rate is per 100,000 ED visits -- a ratio, so it is averaged, never summed.
-- The seven conditions overlap ("Any Mental Health" contains the other six), so
-- summing across condition double-counts; the cube filters to one condition per
-- measure.
--
-- The 'Total' stratum is kept as a row rather than dropped -- it is the correct
-- series to chart on its own -- and is_total lets a demographic breakdown
-- exclude it.
{{ config(materialized="table") }}

select
    condition,
    month_start,
    month_end,
    stratification_category,
    stratification,
    is_total,
    rate_per_100k_visits
from {{ ref('core__ed_visits_mental_health_monthly') }}
where rate_per_100k_visits is not null
