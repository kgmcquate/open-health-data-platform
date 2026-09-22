-- Mart layer: Medicare beneficiary enrollment by geography and coverage type,
-- the presentation table behind the financial cube's enrollment cube.
--
-- Grain: geography level x state x county x year x month. County rows and
-- their parent state/national rows both exist here -- filter to one
-- geography_level before summing beneficiary counts, or a national total
-- ends up double- (or 3,000x-) counted against its own counties.
--
-- Every *_beneficiaries column here is a headcount and sums across
-- geographies (within one geography_level) and across the coverage
-- breakdowns that partition total_beneficiaries (original_medicare +
-- medicare_advantage, or aged + disabled, or the dual/no-dual split, or
-- part_a + part_b) -- but not across two different breakdowns of the same
-- population. This mart keeps the headline coverage, age/disability, dual-
-- eligibility and Part D splits; the source's finer 10-year age bands, race
-- breakdown and Part A/B-only sub-splits are still in
-- stg_cms__medicare_monthly_enrollment for anyone who needs them.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    bene_geo_lvl                                 as geography_level,
    bene_state_abrvtn                            as state_abbr,
    bene_state_desc                              as state_name,
    bene_county_desc                             as county_name,
    bene_fips_cd                                 as county_fips,
    try_cast(year as int)                        as year,
    try_cast(month as int)                       as month,

    -- Headline coverage
    try_cast(tot_benes as double)                 as total_beneficiaries,
    try_cast(orgnl_mdcr_benes as double)          as original_medicare_beneficiaries,
    try_cast(ma_and_oth_benes as double)          as medicare_advantage_beneficiaries,

    -- Aged vs. disabled
    try_cast(aged_tot_benes as double)            as aged_beneficiaries,
    try_cast(dsbld_tot_benes as double)           as disabled_beneficiaries,

    -- Dual Medicare/Medicaid eligibility
    try_cast(dual_tot_benes as double)            as dual_eligible_beneficiaries,
    try_cast(full_dual_tot_benes as double)       as full_dual_eligible_beneficiaries,
    try_cast(part_dual_tot_benes as double)       as partial_dual_eligible_beneficiaries,
    try_cast(nodual_tot_benes as double)          as non_dual_beneficiaries,

    -- Part A / Part B
    try_cast(a_tot_benes as double)               as part_a_beneficiaries,
    try_cast(b_tot_benes as double)                as part_b_beneficiaries,

    -- Part D prescription drug coverage, incl. low-income subsidy (LIS) status
    try_cast(prscrptn_drug_tot_benes as double)          as part_d_beneficiaries,
    try_cast(prscrptn_drug_pdp_benes as double)          as part_d_standalone_plan_beneficiaries,
    try_cast(prscrptn_drug_mapd_benes as double)         as part_d_medicare_advantage_beneficiaries,
    try_cast(prscrptn_drug_full_lis_benes as double)     as part_d_full_lis_beneficiaries,
    try_cast(prscrptn_drug_partial_lis_benes as double)  as part_d_partial_lis_beneficiaries,
    try_cast(prscrptn_drug_no_lis_benes as double)       as part_d_no_lis_beneficiaries,

    ingest_ts
from {{ ref('stg_cms__medicare_monthly_enrollment') }}
where bene_geo_lvl is not null

)

select
    {{ row_sk(['geography_level', 'state_abbr', 'county_fips', 'year', 'month']) }} as row_sk,
    *
from mart
