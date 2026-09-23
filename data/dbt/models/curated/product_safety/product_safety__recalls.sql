-- Mart layer: FDA recall enforcement reports at recall grain, the
-- presentation table behind the product_safety cube.
--
-- Grain: product_type x recall_number. recall_number is already globally
-- unique -- openFDA prefixes it with the issuing centre (`D-`/`F-`/`Z-`) --
-- but product_type rides along in the key so the grain still reads as what it
-- is, three unioned endpoints.
--
-- Kept at recall grain rather than pre-aggregated, the same call
-- access__treatment_sites makes: Cube rolls rows up to a monthly count, but
-- it cannot recover "which drug, recalled by whom, and why" from a count, and
-- the free-text reason_for_recall is the column most recall analysis actually
-- reads. product_safety__recall_trend_monthly is the pre-aggregated
-- counterpart for dashboards that only want the time series.
--
-- One row is one recalled *product*, not one recall action: an event
-- (event_id) routinely covers dozens of products from one firm, so a row
-- count answers "how many products were recalled" and count_distinct over
-- event_id answers "how many recall actions were taken". Both are exposed as
-- measures in the cube; they differ by roughly 4x on drug recalls.
--
-- days_open is computed here rather than in core because it is a
-- reporting-date-relative figure -- it moves every day for a still-open
-- recall -- and core__product_recall's days_to_termination is deliberately
-- the fixed, null-while-open version.
--
-- **The drug-identity columns are a flattened display copy**, not the real
-- shape of that data. Every openFDA attribute is multi-valued (a recall can
-- name four substances and seven package NDCs), so collapsing each to a
-- sorted, distinct, ` | `-delimited string keeps this table at one row per
-- recall at the cost of being ungroupable -- you cannot GROUP BY a string
-- that reads "IBUPROFEN | PSEUDOEPHEDRINE". Group on
-- product_safety__drug_recall_attributes instead, which is the same values
-- in long form; use these for display, for a `contains` filter, and for the
-- counts beside them.
--
-- They are null for ~82% of recalls, which is not a data gap to be fixed:
-- openFDA attaches an SPL match to a minority of recalls and skews toward
-- the less severe ones. has_openfda_identity is the flag that makes the
-- absence explicit, and it is the honest denominator for any share drawn
-- from the attributes mart. Food and device recalls have no openFDA block at
-- all, so they are always false.
{{ config(materialized="table") }}

{% set flattened = [
    ('substance_name', 'substance_names'),
    ('brand_name', 'brand_names'),
    ('generic_name', 'generic_names'),
    ('manufacturer_name', 'manufacturer_names'),
    ('pharm_class_epc', 'pharm_class_epc_names'),
    ('route', 'routes'),
] %}

with identity as (

    select
        recall_number,
        {% for attribute, alias in flattened %}
        -- `order by` inside the aggregate, not just `distinct`: without it
        -- DuckDB returns the values in whatever order it happened to group
        -- them, so the same recall's string changes between builds.
        string_agg(distinct attribute_value, ' | ' order by attribute_value)
            filter (where attribute = '{{ attribute }}')      as {{ alias }},
        {% endfor %}
        count(distinct attribute_value)
            filter (where attribute = 'substance_name')       as substance_count,
        count(distinct attribute_value)
            filter (where attribute = 'product_ndc')          as product_ndc_count
    from {{ ref('core__drug_recall_attribute') }}
    group by 1

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
), mart as (

select
    r.product_type,
    r.recall_number,
    r.event_id,
    r.status,
    r.is_open,
    r.classification,
    r.classification_rank,
    r.product_description,
    r.product_quantity,
    r.reason_for_recall,
    r.code_info,
    r.distribution_pattern,
    r.is_nationwide,
    r.voluntary_mandated,
    r.initial_firm_notification,
    r.recalling_firm,
    r.firm_city,
    r.firm_state,
    r.firm_postal_code,
    r.recall_initiation_date,
    r.recall_initiation_month,
    r.center_classification_date,
    r.report_date,
    r.termination_date,
    r.days_to_termination,
    case
        when r.is_open and r.recall_initiation_date is not null
        then datediff('day', r.recall_initiation_date, current_date())
        else r.days_to_termination
    end                                          as days_open,
    r.ingest_ts,

    -- openFDA's SPL match, flattened for display. See the header.
    i.recall_number is not null                   as has_openfda_identity,
    {% for attribute, alias in flattened %}
    i.{{ alias }},
    {% endfor %}
    coalesce(i.substance_count, 0)                as substance_count,
    coalesce(i.product_ndc_count, 0)              as product_ndc_count
from {{ ref('core__product_recall') }} r
left join identity i on r.recall_number = i.recall_number

)

select
    {{ row_sk(['product_type', 'recall_number']) }} as row_sk,
    *
from mart
