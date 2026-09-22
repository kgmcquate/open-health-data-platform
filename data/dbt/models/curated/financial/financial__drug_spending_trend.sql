-- Mart layer: per-drug Medicare/Medicaid spending trend, the presentation
-- table behind the financial cube.
--
-- Grain: source_dataset x drug_key x year. drug_key is drug_code (Part B's
-- HCPCS code) where the source has one, else brand_name|generic_name (Part D,
-- Medicaid) -- see core__drug_spending.sql for why the three sources don't
-- share one natural drug identifier.
--
-- total_spending_usd is a real dollar total and sums across drugs; the
-- avg_spending_per_* columns are per-unit/per-claim/per-beneficiary averages
-- and don't -- summing avg_spending_per_claim_usd across drugs answers no
-- real question.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    source_dataset,
    drug_key,
    drug_code,
    brand_name,
    generic_name,
    manufacturer_count,
    manufacturer_name,
    year,
    total_spending_usd,
    total_dosage_units,
    total_claims,
    total_beneficiaries,
    avg_spending_per_dosage_unit_usd,
    avg_spending_per_claim_usd,
    avg_spending_per_beneficiary_usd,
    outlier_flag,
    ingest_ts
from {{ ref('core__drug_spending') }}
where total_spending_usd is not null

)

select
    {{ row_sk(['source_dataset', 'drug_key', 'year']) }} as row_sk,
    *
from mart
