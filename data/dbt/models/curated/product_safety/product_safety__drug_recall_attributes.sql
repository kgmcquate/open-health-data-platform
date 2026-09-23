-- Mart layer: the drug behind each recall -- substances, pharmacologic
-- classes, NDC codes and the rest of openFDA's SPL match -- in long form,
-- with enough of the recall's own context attached that a cube can slice
-- recalls by drug identity without a join.
--
-- Grain: recall_number x attribute x value_index.
--
-- This is the table that answers "which substances get recalled, and how
-- severely" -- product_safety__recalls carries the same values flattened to
-- delimited strings for display, which is lossy on purpose and useless for
-- grouping.
--
-- **It is a matched-recalls table, not a recalls table.** Only ~18% of US
-- drug recalls carry an openFDA SPL match at all, and Class I recalls match
-- least often (15.6% against Class III's 34.2%), so counting recalls here
-- understates severe ones twice over. Any share must take its denominator
-- from product_safety__recalls, where every recall is present and
-- has_openfda_identity says which ones reach this table. See
-- core__drug_recall_attribute.
--
-- The recall context columns are denormalised rather than joined at query
-- time for the same reason core__air_quality_monthly denormalises its
-- station attributes: every one of them is something this table gets sliced
-- by, and the parent is small.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    a.recall_number,
    a.attribute_group,
    a.attribute,
    a.attribute_value,
    a.value_index,

    -- The recall's own context, so this table slices without a join.
    r.event_id,
    r.status,
    r.is_open,
    r.classification,
    r.classification_rank,
    r.recalling_firm,
    r.firm_state,
    r.is_nationwide,
    r.recall_initiation_date,
    r.recall_initiation_month,
    r.reason_for_recall,

    a.ingest_ts
from {{ ref('core__drug_recall_attribute') }} a
inner join {{ ref('core__product_recall') }} r
    on a.product_type = r.product_type
    and a.recall_number = r.recall_number

)

select
    {{ row_sk(['recall_number', 'attribute', 'value_index']) }} as row_sk,
    *
from mart
