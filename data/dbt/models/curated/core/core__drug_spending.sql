-- Core layer: one conformed long fact for CMS's three drug-spending datasets
-- (Medicare Part D, Medicare Part B, Medicaid). One row per source x drug x
-- year, 2020-2024.
--
-- All three publish the same shape -- one row per drug, spending/utilization
-- repeated as YYYY-suffixed columns for each year -- so unpivoting each to a
-- year row and unioning the three is the same move core__health_indicator
-- makes for CDC's four indicator sources.
--
-- The three datasets don't key drugs the same way. Part D and Medicaid key on
-- (brand_name, generic_name); Part B keys on HCPCS code (a drug can share a
-- code across brand/generic pairs), so drug_code is null for the other two
-- and drug_key is the column to group on across sources. Medicaid has no
-- total_beneficiaries or avg_spending_per_beneficiary_usd -- CMS doesn't
-- publish a beneficiary count for it -- so those are null on
-- medicaid_spending_by_drug rows, not zero.
--
-- Part D and Medicaid are published one row per drug per *manufacturer*, plus
-- an 'Overall' roll-up row per drug -- so the wide source is not one row per
-- drug the way Part B is, and every (brand_name, generic_name) pair has
-- exactly one 'Overall' row. This model keeps only that roll-up: mixing it
-- with its own parts would both break the grain above and make
-- total_spending_usd double-count under any SUM (Cube sums it), and the parts
-- do add up to it -- summed over Part D's 3,625 drugs, the manufacturer rows
-- and the Overall rows agree to the cent. manufacturer_count (CMS's tot_mftr)
-- carries how many manufacturers were rolled up; per-manufacturer detail stays
-- in stg_cms__medicare_part_d_spending_by_drug /
-- stg_cms__medicaid_spending_by_drug for anyone who needs it, and there is
-- deliberately no manufacturer_name column here -- it would read 'Overall' on
-- every row it is populated for.
--
-- outlier_flag is CMS's own text flag on the source data (average spending
-- per dosage unit far from the drug's historical trend); kept as-is rather
-- than cast to boolean since data-api/v1 gives every column back as a string
-- with no documented enumeration.
--
-- Dropped: each source's trailing chg_avg_spnd_..._23_24 /
-- cagr_avg_spnd_..._20_24 columns. They're a 2023-2024 change and a
-- 2020-2024 compound growth rate computed once per drug, not a per-year
-- figure -- they don't fit this mart's year grain and are easy to recompute
-- from the year rows here if a mart ever needs them.
{{ config(materialized="table") }}

{% set years = [2020, 2021, 2022, 2023, 2024] %}

with part_d as (

    {% for year in years %}
    select
        'medicare_part_d'                                   as source_dataset,
        cast(null as varchar)                                as drug_code,
        brnd_name                                            as brand_name,
        gnrc_name                                            as generic_name,
        try_cast(tot_mftr as int)                            as manufacturer_count,
        {{ year }}                                           as year,
        try_cast(tot_spndng_{{ year }} as double)             as total_spending_usd,
        try_cast(tot_dsg_unts_{{ year }} as double)           as total_dosage_units,
        try_cast(tot_clms_{{ year }} as double)               as total_claims,
        try_cast(tot_benes_{{ year }} as double)              as total_beneficiaries,
        try_cast(avg_spnd_per_dsg_unt_wghtd_{{ year }} as double) as avg_spending_per_dosage_unit_usd,
        try_cast(avg_spnd_per_clm_{{ year }} as double)       as avg_spending_per_claim_usd,
        try_cast(avg_spnd_per_bene_{{ year }} as double)      as avg_spending_per_beneficiary_usd,
        nullif(outlier_flag_{{ year }}, '')                  as outlier_flag,
        ingest_ts
    from {{ ref('stg_cms__medicare_part_d_spending_by_drug') }}
    where mftr_name = 'Overall'
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

), part_b as (

    {% for year in years %}
    select
        'medicare_part_b'                                   as source_dataset,
        hcpcs_cd                                             as drug_code,
        brnd_name                                            as brand_name,
        gnrc_name                                            as generic_name,
        cast(null as int)                                    as manufacturer_count,
        {{ year }}                                           as year,
        try_cast(tot_spndng_{{ year }} as double)             as total_spending_usd,
        try_cast(tot_dsg_unts_{{ year }} as double)           as total_dosage_units,
        try_cast(tot_clms_{{ year }} as double)               as total_claims,
        try_cast(tot_benes_{{ year }} as double)              as total_beneficiaries,
        try_cast(avg_spndng_per_dsg_unt_{{ year }} as double) as avg_spending_per_dosage_unit_usd,
        try_cast(avg_spndng_per_clm_{{ year }} as double)     as avg_spending_per_claim_usd,
        try_cast(avg_spndng_per_bene_{{ year }} as double)    as avg_spending_per_beneficiary_usd,
        nullif(outlier_flag_{{ year }}, '')                  as outlier_flag,
        ingest_ts
    from {{ ref('stg_cms__medicare_part_b_spending_by_drug') }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

), medicaid as (

    {% for year in years %}
    select
        'medicaid'                                          as source_dataset,
        cast(null as varchar)                                as drug_code,
        brnd_name                                            as brand_name,
        gnrc_name                                            as generic_name,
        try_cast(tot_mftr as int)                            as manufacturer_count,
        {{ year }}                                           as year,
        try_cast(tot_spndng_{{ year }} as double)             as total_spending_usd,
        try_cast(tot_dsg_unts_{{ year }} as double)           as total_dosage_units,
        try_cast(tot_clms_{{ year }} as double)               as total_claims,
        cast(null as double)                                 as total_beneficiaries,
        try_cast(avg_spnd_per_dsg_unt_wghtd_{{ year }} as double) as avg_spending_per_dosage_unit_usd,
        try_cast(avg_spnd_per_clm_{{ year }} as double)       as avg_spending_per_claim_usd,
        cast(null as double)                                 as avg_spending_per_beneficiary_usd,
        nullif(outlier_flag_{{ year }}, '')                  as outlier_flag,
        ingest_ts
    from {{ ref('stg_cms__medicaid_spending_by_drug') }}
    where mftr_name = 'Overall'
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

)

select *, coalesce(drug_code, brand_name || '|' || generic_name) as drug_key from part_d
union all select *, coalesce(drug_code, brand_name || '|' || generic_name) as drug_key from part_b
union all select *, coalesce(drug_code, brand_name || '|' || generic_name) as drug_key from medicaid
