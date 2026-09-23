-- Core layer: one conformed fact for openFDA's three enforcement endpoints
-- (drug, food, device). One row per recall_number, i.e. per recalled
-- *product* -- not per recall event, which is what event_id groups (one event
-- routinely covers many products; see models/raw/_stg_openfda__sources.yml).
--
-- All three endpoints return the identical 24 fields, so unioning them is
-- cheaper than core__drug_spending's unpivot-then-union: nothing has to be
-- reshaped, only typed. `product_type` is already constant within each source
-- table, but it is set from a literal here rather than carried through, so a
-- row's provenance survives openFDA ever changing what it puts in that field.
--
-- Everything openFDA publishes is text (its API serialises every field as a
-- JSON string, the same way Socrata's SODA API does), so this model is where
-- the dates become dates -- macros/yyyymmdd_to_date.sql -- and where the
-- derived analysis columns are computed once for every mart downstream:
--
--   * classification_rank -- `Class I`/`II`/`III` as 1/2/3 so severity sorts
--     and filters numerically. Null for drug rows carrying openFDA's
--     undocumented `Not Yet Classified`, which is genuinely unknown severity
--     rather than a fourth, less severe class.
--   * is_nationwide -- distribution_pattern is free text ("Distributors in 6
--     states: NY, VA, ..." / "Nationwide"), so the only reliably machine-
--     readable thing in it is whether it says nationwide.
--   * days_to_termination / is_open -- a recall's duration, and the honest
--     reading of a null termination_date, which means "still running", not
--     "missing". days_to_termination is therefore null while open rather
--     than measured against today: a mart that wants "days open so far"
--     should compute it from recall_initiation_date itself.
--
-- Kept as published: status, voluntary_mandated and
-- initial_firm_notification carry `N/A` and empty strings alongside their
-- real values, and outlier-flag-style normalisation is a mart decision -- the
-- empty strings are nulled, the vocabularies are not collapsed.
--
-- Not carried forward: nothing. All 24 source fields survive, because the
-- table is small (tens of thousands of rows) and the free-text fields
-- (reason_for_recall, code_info, product_description) are exactly what recall
-- analysis reads.
{{ config(materialized="table") }}

{% set endpoints = [
    ('drug', 'stg_openfda__drug_enforcement'),
    ('food', 'stg_openfda__food_enforcement'),
    ('device', 'stg_openfda__device_enforcement'),
] %}

with unioned as (

    {% for product_type, model in endpoints %}
    select
        '{{ product_type }}'                         as product_type,
        recall_number,
        event_id,
        nullif(status, '')                           as status,
        nullif(classification, '')                   as classification,
        nullif(product_description, '')              as product_description,
        nullif(product_quantity, '')                 as product_quantity,
        nullif(code_info, '')                        as code_info,
        nullif(more_code_info, '')                   as more_code_info,
        nullif(reason_for_recall, '')                as reason_for_recall,
        nullif(distribution_pattern, '')             as distribution_pattern,
        nullif(voluntary_mandated, '')               as voluntary_mandated,
        nullif(initial_firm_notification, '')        as initial_firm_notification,
        nullif(recalling_firm, '')                   as recalling_firm,
        nullif(address_1, '')                        as firm_address_1,
        nullif(address_2, '')                        as firm_address_2,
        nullif(city, '')                             as firm_city,
        nullif(state, '')                            as firm_state,
        nullif(postal_code, '')                      as firm_postal_code,
        nullif(country, '')                          as firm_country,
        {{ yyyymmdd_to_date('recall_initiation_date') }}   as recall_initiation_date,
        {{ yyyymmdd_to_date('center_classification_date') }} as center_classification_date,
        {{ yyyymmdd_to_date('report_date') }}              as report_date,
        {{ yyyymmdd_to_date('termination_date') }}         as termination_date,
        ingest_ts
    from {{ ref(model) }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

)

select
    *,
    case classification
        when 'Class I' then 1
        when 'Class II' then 2
        when 'Class III' then 3
    end                                                      as classification_rank,
    lower(coalesce(distribution_pattern, '')) like '%nationwide%' as is_nationwide,
    termination_date is null                                 as is_open,
    case
        when termination_date is not null and recall_initiation_date is not null
        then datediff('day', recall_initiation_date, termination_date)
    end                                                      as days_to_termination,
    date_trunc('month', recall_initiation_date)::date        as recall_initiation_month
from unioned
