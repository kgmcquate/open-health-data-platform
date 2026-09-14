-- Mart layer: NHSN weekly hospital respiratory burden by jurisdiction.
--
-- Grain: jurisdiction x week. Excludes the 'USA' national row so a sum across
-- jurisdictions is the country rather than twice the country; the national
-- series stays in core__hospital_respiratory_weekly.
--
-- Admissions are counts (additive); the bed percentages are ratios and must be
-- averaged. pct_days_reporting is carried through because reporting became
-- voluntary in May 2024 -- a fall in admissions after that date may be a fall in
-- *reporting*, and this column is the only way to tell the two apart.
{{ config(materialized="table") }}

select
    jurisdiction,
    week_end,
    admissions_covid,
    admissions_covid_adult,
    admissions_covid_pediatric,
    admissions_influenza,
    avg_daily_admissions_covid,
    avg_daily_admissions_influenza,
    pct_inpatient_beds_covid,
    pct_icu_beds_covid,
    pct_inpatient_beds_influenza,
    pct_icu_beds_influenza,
    pct_inpatient_beds_occupied,
    pct_icu_beds_occupied,
    pct_days_reporting
from {{ ref('core__hospital_respiratory_weekly') }}
where not is_national
