-- Core layer: NHSN weekly hospital respiratory burden by jurisdiction.
--
-- The source has 82 columns -- every metric crossed with adult/paediatric,
-- confirmed/suspected, counts/percentages, plus per-metric hospital-reporting
-- denominators and week-over-week deltas. This selects the load-bearing subset:
-- total admissions, current inpatient and ICU census, and bed occupancy, for
-- COVID-19 and influenza. The rest stays available in the clean layer.
--
-- `jurisdiction` includes a 'USA' national row alongside the states and
-- territories, so is_national flags it rather than dropping it -- summing
-- across jurisdictions without that filter double-counts the country.
{{ config(materialized="table") }}

select
    jurisdiction,
    jurisdiction = 'USA'                        as is_national,
    cast(week_end_date as date)                 as week_end,

    try_cast(total_admissions_all_covid_confirmed as double)      as admissions_covid,
    try_cast(total_admissions_adult_covid_confirmed as double)    as admissions_covid_adult,
    try_cast(total_admissions_pediatric_covid_confirmed as double) as admissions_covid_pediatric,
    try_cast(total_admissions_all_influenza_confirmed as double)  as admissions_influenza,

    try_cast(avg_admissions_all_covid_confirmed as double)        as avg_daily_admissions_covid,
    try_cast(avg_admissions_all_influenza_confirmed as double)    as avg_daily_admissions_influenza,

    try_cast(avg_percent_inpatient_beds_covid as double)          as pct_inpatient_beds_covid,
    try_cast(avg_percent_staff_icu_beds_covid as double)          as pct_icu_beds_covid,
    try_cast(avg_percent_inpatient_beds_influenza as double)      as pct_inpatient_beds_influenza,
    try_cast(avg_percent_icu_beds_influenza as double)            as pct_icu_beds_influenza,
    try_cast(avg_percent_inpatient_beds_occupied as double)       as pct_inpatient_beds_occupied,
    try_cast(avg_percent_staff_icu_beds_occupied as double)       as pct_icu_beds_occupied,

    -- Reporting completeness: these metrics are voluntary from May 2024, so a
    -- low value here means "few hospitals reported", not "little disease".
    try_cast(weekly_percent_days_reporting_any_data as double)    as pct_days_reporting,
    ingest_ts
from {{ ref('stg_cdc__hospitalization_metrics') }}
where week_end_date is not null
