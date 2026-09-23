-- Core layer: the openFDA drug-identity attributes attached to a recall,
-- conformed to one long table. One row per recall_number x attribute x
-- value_index.
--
-- Long rather than 21 wide columns because every one of these attributes is
-- multi-valued: a recall can name four substances, seven package NDCs and one
-- brand. Pivoting them would force either an arbitrary "first" value or 21
-- delimited strings, and both throw away the thing the child tables exist to
-- preserve. The wide, one-row-per-recall summary that dashboards want is
-- built from this table in product_safety__recalls, where the lossiness is a
-- deliberate display choice rather than the only shape available.
--
-- **This table covers ~18% of drug recalls, and not a random 18%.** openFDA
-- attaches an `openfda` block only where it can match the recalled product to
-- an SPL drug listing; in a 5,000-record sample 18.1% carried one, and the
-- match rate falls as severity rises (34.2% of Class III against 15.6% of
-- Class I). So a count of recalls *in this table* is a count of matched
-- recalls. Anything asking "how many recalls involved substance X" is
-- answerable; anything asking "what share of all recalls involved X" is not,
-- because the denominator lives in core__product_recall and most of it is
-- missing here. Downstream models keep the unmatched recalls and flag them
-- rather than filtering them away.
--
-- attribute_group is this model's own addition, not openFDA's: the 21
-- members fall into five families, and the group is what makes a single
-- dimension usable -- "show me the pharmacologic classes" is one filter
-- instead of four.
--
-- value_index is each value's position in its own array. It is carried
-- because it preserves openFDA's ordering within a member, and it is NOT a
-- join key across members: the arrays are ragged (36 of 38 blocks in a
-- 200-record sample had members of differing lengths), so pairing
-- brand_name[0] with product_ndc[0] invents a fact.
--
-- Drug only. Food and device enforcement return no `openfda` block, so there
-- is nothing to conform for them -- product_type is fixed at 'drug' here so
-- the table still joins cleanly to core__product_recall's composite key.
--
-- recall_key is built the same way core__product_recall builds it, off the
-- recall_number and event_id the clean child tables carry: the real recall
-- number where FDA has issued one, else product type plus event id. It has
-- to be recomputed rather than joined for, because the join to
-- core__product_recall is itself on recall_key.
{{ config(materialized="table") }}

{% set attribute_groups = [
    ('drug_identity', ['brand_name', 'generic_name', 'substance_name', 'unii', 'manufacturer_name']),
    ('pharmacologic_class', ['pharm_class_epc', 'pharm_class_moa', 'pharm_class_pe', 'pharm_class_cs', 'nui']),
    ('product_code', ['application_number', 'product_ndc', 'package_ndc', 'rxcui', 'spl_id', 'spl_set_id', 'upc']),
    ('product_form', ['route', 'product_type']),
    ('packaging', ['is_original_packager', 'original_packager_product_ndc']),
] %}

{#- Flattened to one list so the loop below has a single `loop.last` to hang
    the trailing `union all` off, the same shape core__product_recall uses. -#}
{% set members = [] %}
{% for group, attributes in attribute_groups %}
{% for attribute in attributes %}
{% do members.append((group, attribute)) %}
{% endfor %}
{% endfor %}

select
    *
from (
    {% for group, attribute in members %}
    select
        'drug'                              as product_type,
        nullif(nullif(recall_number, ''), 'N/A') as recall_number,
        coalesce(
            nullif(nullif(recall_number, ''), 'N/A'),
            'drug-EVENT-' || event_id
        )                                   as recall_key,
        event_id,
        '{{ group }}'                       as attribute_group,
        '{{ attribute }}'                   as attribute,
        -- cast: every member is text except is_original_packager, which dlt
        -- typed boolean off the JSON. One column has to hold both.
        cast({{ attribute }} as varchar)    as attribute_value,
        value_index,
        ingest_ts
    from {{ ref('stg_openfda__drug_enforcement__' ~ attribute) }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
)
