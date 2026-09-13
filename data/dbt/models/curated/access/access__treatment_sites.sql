-- Mart layer: therapeutic access, at provider-site grain so the same table
-- serves both a map (one pin per site) and a state rollup (count of sites).
--
-- Kept at site grain rather than pre-aggregated to state: Cube can roll site
-- rows up to a state count, but it cannot recover individual sites from a
-- state total, and the map is the primary consumer.
--
-- `is_stale_report` is the honest caveat on every stock flag here: the locator
-- carries a site's last self-reported inventory date, and a site that stopped
-- reporting keeps its last known flags indefinitely.
{{ config(materialized="table") }}

select
    site_id,
    provider_name,
    city,
    state,
    zip_code,
    latitude,
    longitude,
    appointment_url,
    last_report_date,
    datediff(day, last_report_date, current_date()) as days_since_report,
    datediff(day, last_report_date, current_date()) > 14 as is_stale_report,
    treats_covid,
    treats_flu,
    has_prescribing_services,
    has_home_delivery,
    has_paxlovid,
    has_lagevrio,
    has_veklury,
    has_oseltamivir_tamiflu,
    has_oseltamivir_generic,
    has_baloxavir,
    has_any_covid_therapeutic,
    has_any_flu_therapeutic
from {{ ref('core__treatment_site') }}
