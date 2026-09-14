-- Core layer: NWSS public SARS-CoV-2 wastewater metrics, one row per sewershed
-- (key_plot_id) per reporting date.
--
-- Distinct from core__wastewater_viral_activity_weekly, which carries CDC's
-- normalised cross-pathogen activity level: this table is SARS-CoV-2 only and
-- carries the two metrics CDC's own trend maps are built from --
--   ptc_15d       15-day percent change in viral concentration
--   detect_prop_15d  share of samples with detectable virus over 15 days
-- plus `percentile`, the site's concentration ranked against its own history.
--
-- Every numeric here is published as text (Socrata serialises all fields as
-- strings and CDC uses suppression markers in these columns), hence try_cast
-- throughout.
{{ config(materialized="table") }}

select
    key_plot_id                                 as site_key,
    wwtp_id                                     as treatment_plant_id,
    wwtp_jurisdiction                           as jurisdiction,
    reporting_jurisdiction,
    county_names,
    county_fips,
    sample_location,
    sample_location_specify,
    try_cast(population_served as bigint)       as population_served,
    try_to_date(date_start)                     as period_start,
    try_to_date(date_end)                       as period_end,
    try_cast(ptc_15d as double)                 as pct_change_15d,
    try_cast(detect_prop_15d as double)         as detection_proportion_15d,
    try_cast(percentile as double)              as concentration_percentile,
    sampling_prior,
    try_to_date(first_sample_date)              as first_sample_date,
    ingest_ts
from {{ ref('stg_cdc__wastewater_sars_cov_2_metrics') }}
where try_to_date(date_end) is not null
