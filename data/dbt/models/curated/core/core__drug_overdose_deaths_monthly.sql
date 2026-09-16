-- Core layer: VSRR provisional drug overdose deaths, one row per state x month
-- x drug-class indicator.
--
-- Every value is a **12-month-ending** count, not a monthly count -- `period`
-- is '12 month-ending' for all 86k rows. That makes the series a rolling
-- annual total: summing consecutive months would multiply the same deaths
-- twelve times over, so `is_rolling_12_month` is carried as an explicit warning
-- for anyone writing a measure against this, and the mart aggregates with max /
-- latest rather than sum.
--
-- `indicator` mixes two kinds of row: overall counts ("Number of Drug Overdose
-- Deaths") and per-drug-class counts keyed by ICD-10 T-code
-- ("Opioids (T40.0-T40.4,T40.6)"). is_drug_class separates them so a total and
-- its own components are never added together.
{{ config(materialized="table") }}

select
    state                                       as state_abbr,
    state_name,
    try_cast(year as int)                       as year,
    month                                       as month_name,
    -- CDC publishes the month as a full English name ('February'). Mapped
    -- explicitly rather than through a to_date format model, so the parse can
    -- neither depend on session locale nor fail silently to null.
    make_date(
        try_cast(year as int),
        case month
            when 'January' then 1 when 'February' then 2 when 'March'     then 3
            when 'April'   then 4 when 'May'      then 5 when 'June'      then 6
            when 'July'    then 7 when 'August'   then 8 when 'September' then 9
            when 'October' then 10 when 'November' then 11 when 'December' then 12
        end,
        1
    )                                           as month_start,
    indicator,
    indicator not in (
        'Number of Deaths', 'Number of Drug Overdose Deaths',
        'Percent with drugs specified'
    )                                           as is_drug_class,
    period,
    period = '12 month-ending'                  as is_rolling_12_month,
    try_cast(data_value as double)              as deaths,
    try_cast(predicted_value as double)         as predicted_deaths,
    try_cast(percent_pending_investigation as double) as percent_pending_investigation,
    footnote,
    ingest_ts
from {{ ref('stg_cdc__drug_overdose_deaths') }}
