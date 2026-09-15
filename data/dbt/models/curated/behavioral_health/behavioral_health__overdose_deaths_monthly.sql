-- Mart layer: provisional drug overdose deaths by state and month.
--
-- Grain: state x month x indicator.
--
-- **Every value is a 12-month-ending rolling total**, so this mart must never
-- be summed over months -- twelve consecutive rows describe largely the same
-- deaths. The cube exposes it through max / latest measures only, and
-- `is_rolling_12_month` is carried as a standing warning to anyone writing a
-- new one.
--
-- Excludes the national 'US' row so a sum across states is the country once.
-- `is_drug_class` separates the per-drug-class rows (keyed by ICD-10 T-code)
-- from the overall counts, because the classes overlap each other *and* roll up
-- into the total.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    state_abbr,
    state_name,
    month_start,
    year,
    indicator,
    is_drug_class,
    is_rolling_12_month,
    deaths                                      as deaths_12_month_ending,
    predicted_deaths                            as predicted_deaths_12_month_ending,
    percent_pending_investigation,
    ingest_ts
from {{ ref('core__drug_overdose_deaths_monthly') }}
where state_abbr <> 'US'
  and month_start is not null

)

select
    {{ row_sk(['state_abbr', 'month_start', 'indicator']) }} as row_sk,
    *
from mart
