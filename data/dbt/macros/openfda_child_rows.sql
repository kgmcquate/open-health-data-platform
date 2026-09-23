{#
    The clean-layer body every openFDA *child* staging model shares --
    dlt's split of drug/enforcement's nested `openfda` object, one table per
    array member (see models/raw/_stg_openfda__sources.yml's header).

    **This macro exists to resolve `_dlt_parent_id` = the parent's `_dlt_id`
    here, once, so nothing downstream ever sees a dlt surrogate key.** A raw
    child row carries only its value, its position in the array and an opaque
    pointer at the parent; on its own it cannot say which recall it belongs
    to. Joining back to DRUG_ENFORCEMENT turns that pointer into
    `recall_number` -- the business key every other openFDA model is keyed
    on -- so each clean child table stands on its own and a mart can union or
    join them without reaching back into RAW.

    It also supplies `ingest_ts`, which a child table genuinely cannot: dlt
    stamps `_dlt_load_id` on root tables only, so the load timestamp has to
    come across the join too.

    Dedupe rides on the same join. The parent CTE reduces to the latest load
    per `recall_number` exactly as macros/openfda_current_rows.sql does, and
    because each load writes a *fresh* set of child rows pointing at that
    load's parent `_dlt_id`, an inner join against the deduped parent drops
    the superseded load's children for free -- no second `qualify` here. If a
    recall's `openfda` block changed between loads, what survives is the newer
    block, whole, rather than a mix of both.

    Rows whose parent did not survive the dedupe, and rows for the ~82% of
    recalls with no `openfda` block at all, simply are not here -- the tables
    are sparse by nature. Do not read a missing recall as a recall with no
    substances; read it as a recall openFDA could not match to a drug listing.

    This reads RAW's parent table rather than the clean one, and has to:
    macros/openfda_current_rows.sql drops `_dlt_id`, which is the very column
    the join needs. So the parent dedupe is repeated here rather than reused.

    Column case: raw identifiers are upper-cased (ohdp_ingestion/sql_upper.py,
    ADR-0019). Because the parent is read from RAW, `recall_number` needs an
    explicit alias below or it lands in the clean table as `RECALL_NUMBER` --
    DuckDB resolves the unquoted reference case-insensitively but names the
    output column after the *stored* name, and Iceberg then preserves that
    upper case for Cube to fail to resolve (see globals.py's note on quoted
    identifiers). The parent model's own lower-casing does not help here,
    since this does not go through it. Every other column below is already
    aliased, so a child table's fixed three-column shape needs no
    `adapter.quote` loop the way cms_current_rows/openaq_current_rows do.

    `value` is aliased to the attribute's own name, so the clean table reads
    as what it holds:

        {{ openfda_child_rows('substance_name') }}
#}
{% macro openfda_child_rows(attribute, parent_table='drug_enforcement') -%}
with parent as (

    select
        _dlt_id,
        recall_number,
        {{ dlt_load_id_as_ts() }} as ingest_ts
    from {{ source('openfda', parent_table) }}
    qualify row_number() over (
        partition by recall_number
        order by _dlt_load_id desc
    ) = 1

)

select
    -- Explicitly aliased, not bare: see the column-case note above.
    parent.recall_number   as recall_number,
    child.value            as {{ attribute }},
    child._dlt_list_idx    as value_index,
    parent.ingest_ts
from {{ source('openfda', parent_table ~ '__openfda__' ~ attribute) }} as child
inner join parent
    on child._dlt_parent_id = parent._dlt_id
{%- endmacro %}
