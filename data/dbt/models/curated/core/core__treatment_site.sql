-- Core layer: ASPR Treatments Locator, one row per dispensing provider site.
--
-- Socrata `checkbox` columns arrive as booleans; the per-drug flags are kept
-- individually (a site can stock any combination) and rolled up into two
-- convenience booleans so the common "does this site treat COVID / flu at all"
-- question does not require OR-ing nine columns in every downstream query.
{{ config(materialized="table") }}

select
    socrata_id::varchar as site_id,
    provider_name::varchar as provider_name,
    address1::varchar as address_line_1,
    address2::varchar as address_line_2,
    city::varchar as city,
    upper(trim(state))::varchar as state,
    zip::varchar as zip_code,
    public_phone::varchar as public_phone,
    url_appointment::varchar as appointment_url,
    grantee_code::varchar as grantee_code,
    latitude::float as latitude,
    longitude::float as longitude,
    cast(last_report_date as date) as last_report_date,

    is_covid::boolean as treats_covid,
    is_flu::boolean as treats_flu,
    is_icatt_site::boolean as is_icatt_site,
    is_pap::boolean as is_patient_assistance_program,
    is_prescribing_svcs_available::boolean as has_prescribing_services,
    home_delivery::boolean as has_home_delivery,

    has_paxlovid::boolean as has_paxlovid,
    has_lagevrio::boolean as has_lagevrio,
    has_veklury::boolean as has_veklury,
    has_oseltamivir_tamiflu::boolean as has_oseltamivir_tamiflu,
    has_oseltamivir_generic::boolean as has_oseltamivir_generic,
    has_oseltamivir_suspension::boolean as has_oseltamivir_suspension,
    has_baloxavir::boolean as has_baloxavir,
    has_zanamivir::boolean as has_zanamivir,
    has_peramivir::boolean as has_peramivir,

    has_usg_product::boolean as has_usg_supplied_product,
    has_commercial_product::boolean as has_commercial_product,

    coalesce(has_paxlovid, false)
        or coalesce(has_lagevrio, false)
        or coalesce(has_veklury, false) as has_any_covid_therapeutic,
    coalesce(has_oseltamivir_tamiflu, false)
        or coalesce(has_oseltamivir_generic, false)
        or coalesce(has_oseltamivir_suspension, false)
        or coalesce(has_baloxavir, false)
        or coalesce(has_zanamivir, false)
        or coalesce(has_peramivir, false) as has_any_flu_therapeutic
from {{ ref('stg_healthdata_gov__treatments_locator') }}
where state is not null
