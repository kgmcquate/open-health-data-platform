{#
    Snowflake's `initcap` with no DuckDB equivalent: upper-case the first
    letter of each whitespace-separated word, lower-case the rest. Used on
    reporting-area and learning-modality labels, which upstream publishes in
    all caps.

    DuckDB has no title-case builtin, so this splits on spaces and rebuilds.
    Nulls pass through; a zero-length word survives the slice (`''[2:]` is
    `''`).
#}
{% macro initcap(expr) -%}
array_to_string(
    list_transform(
        string_split({{ expr }}, ' '),
        word -> upper(word[1]) || lower(word[2:])
    ),
    ' '
)
{%- endmacro %}
