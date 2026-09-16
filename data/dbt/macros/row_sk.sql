{#
    The surrogate key every curated mart carries, and the only column the
    semantic layer marks as a Cube primary key.

    Why a mart needs one at all: Cube requires a primary key on any cube that
    is joined or pre-aggregated, and -- the part that bites -- a dimension
    marked `primary_key: true` has its `public` default flipped to false, so
    Cube drops it from /meta and no client can group or filter by it. These
    marts key on composite *natural* keys (year, week_end, state_abbr,
    measure_id...), which are exactly the axes the semantic layer exists to
    slice by, so marking them as the key hid them -- at its worst,
    chronic_disease__state_indicator_trend exposed 5 of its 14 dimensions and
    no time axis at all. Hashing the natural key into one opaque column moves
    Cube's primary key onto something nobody wants to group by and leaves every
    natural-key column a plain, public dimension. See
    semantic/cube/model/globals.py.

    It doubles as the grain test: `unique` on row_sk is exactly what
    dbt_utils.unique_combination_of_columns asserted over the same list, so a
    mart now declares its grain once, here in the SQL, instead of repeating the
    column list in a `constraints:` block and again in a yml test. The
    natural-key columns carry `meta: {grain: true}` so the grain stays
    discoverable in the manifest and through Cube's /meta.

    generate_surrogate_key rather than a bare concat because several of these
    natural keys are nullable -- chronic_disease__state_indicator_trend's
    stratification_2 is null on every row that is not a two-way cross-tab --
    and it coalesces nulls to a sentinel instead of hashing to null.

    Call it over the mart's *output* column names, from a wrapping `mart` CTE,
    so the hash is built from the same names the grain is documented under
    rather than from whatever the upstream called them.
#}
{% macro row_sk(columns) -%}
{{ dbt_utils.generate_surrogate_key(columns) }}
{%- endmacro %}
