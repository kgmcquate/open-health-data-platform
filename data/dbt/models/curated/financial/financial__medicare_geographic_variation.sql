-- Mart layer: Medicare spending and utilization by geography, the
-- presentation table behind the financial cube's geographic-variation cube.
--
-- Grain: geography level x geography (code AND name) x age group x year.
-- The name is part of the key, not decoration: bene_geo_cd is blank on the
-- National row and on the two catch-all State rows CMS publishes for
-- residence it can't place in a state -- 'Territory' and 'ZZ' -- so those two
-- collide on code alone (every measure on them is CMS's '*' suppression
-- marker, i.e. null after the casts below, but they are still two distinct
-- rows). CMS's source has
-- ~230 measure columns (payment, standardized payment, utilization rate and
-- per-user figures for every care setting down to ambulance and DME, plus 18
-- age-banded preventable-hospitalization (PQI) rates); this mart keeps the
-- headline spending total and the utilization rates for the major care
-- settings (inpatient, readmissions, ER, outpatient, SNF, home health,
-- hospice, DME) rather than reproducing every column CMS publishes. The full
-- set is still in stg_cms__medicare_geographic_variation for anyone who needs
-- a setting not carried forward here.
--
-- *_pymt_amt is a total dollar amount and sums across geographies (careful:
-- county rows and their parent state row both exist here, so summing across
-- bene_geo_lvl double-counts -- filter to one level first). *_pymt_pc
-- ("per capita") and *_per_1000_benes are rates and should be averaged or
-- mapped, never summed. *_stdzd_pymt_pc is the price-standardized version --
-- the one to use when comparing utilization across geographies rather than
-- cost of living.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    bene_geo_lvl                                as geography_level,
    bene_geo_desc                                as geography_name,
    bene_geo_cd                                  as geography_code,
    bene_age_lvl                                 as age_group,
    try_cast(year as int)                        as year,

    -- Beneficiary population and demographics
    try_cast(benes_total_cnt as double)          as total_beneficiaries,
    try_cast(benes_wth_ptaptb_cnt as double)     as beneficiaries_part_a_and_b,
    try_cast(benes_om_cnt as double)              as beneficiaries_original_medicare,
    try_cast(benes_ma_cnt as double)              as beneficiaries_medicare_advantage,
    try_cast(ma_prtcptn_rate as double)           as medicare_advantage_participation_rate,
    try_cast(bene_avg_age as double)              as avg_beneficiary_age,
    try_cast(bene_feml_pct as double)             as pct_female,
    try_cast(bene_race_wht_pct as double)         as pct_white,
    try_cast(bene_race_black_pct as double)       as pct_black,
    try_cast(bene_race_hspnc_pct as double)       as pct_hispanic,
    try_cast(bene_race_othr_pct as double)        as pct_other_race,
    try_cast(bene_dual_pct as double)             as pct_dual_eligible,

    -- Total Medicare spending
    try_cast(tot_mdcr_pymt_amt as double)         as total_medicare_payment_usd,
    try_cast(tot_mdcr_stdzd_pymt_amt as double)   as total_medicare_standardized_payment_usd,
    try_cast(tot_mdcr_pymt_pc as double)          as medicare_payment_per_capita_usd,
    try_cast(tot_mdcr_stdzd_pymt_pc as double)    as medicare_standardized_payment_per_capita_usd,

    -- Inpatient hospital
    try_cast(ip_mdcr_pymt_pc as double)           as inpatient_payment_per_capita_usd,
    try_cast(ip_mdcr_stdzd_pymt_pc as double)     as inpatient_standardized_payment_per_capita_usd,
    try_cast(benes_ip_pct as double)              as pct_beneficiaries_with_inpatient_stay,
    try_cast(ip_cvrd_stays_per_1000_benes as double) as inpatient_stays_per_1000_beneficiaries,
    try_cast(acute_hosp_readmsn_pct as double)    as acute_readmission_pct,

    -- Emergency department
    try_cast(er_visits_per_1000_benes as double)  as er_visits_per_1000_beneficiaries,
    try_cast(benes_er_visits_pct as double)       as pct_beneficiaries_with_er_visit,

    -- Outpatient
    try_cast(op_mdcr_pymt_pc as double)           as outpatient_payment_per_capita_usd,
    try_cast(benes_op_pct as double)              as pct_beneficiaries_with_outpatient_visit,
    try_cast(op_visits_per_1000_benes as double)  as outpatient_visits_per_1000_beneficiaries,

    -- Skilled nursing facility
    try_cast(benes_snf_pct as double)             as pct_beneficiaries_with_snf_stay,
    try_cast(snf_cvrd_stays_per_1000_benes as double) as snf_stays_per_1000_beneficiaries,

    -- Home health
    try_cast(benes_hh_pct as double)              as pct_beneficiaries_with_home_health,
    try_cast(hh_episodes_per_1000_benes as double) as home_health_episodes_per_1000_beneficiaries,

    -- Hospice
    try_cast(benes_hospc_pct as double)           as pct_beneficiaries_with_hospice,
    try_cast(hospc_cvrd_stays_per_1000_benes as double) as hospice_stays_per_1000_beneficiaries,

    -- Durable medical equipment
    try_cast(benes_dme_pct as double)             as pct_beneficiaries_with_dme,
    try_cast(dme_evnts_per_1000_benes as double)  as dme_events_per_1000_beneficiaries,

    ingest_ts
from {{ ref('stg_cms__medicare_geographic_variation') }}
where bene_geo_lvl is not null

)

select
    {{ row_sk(['geography_level', 'geography_code', 'geography_name', 'age_group', 'year']) }} as row_sk,
    *
from mart
