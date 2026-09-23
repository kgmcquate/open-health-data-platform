{#
    Parse openFDA's 8-character `YYYYMMDD` date strings to a real date.

    Every date field on openFDA's enforcement endpoints arrives as text --
    `20260115`, not `2026-01-15` -- so macros/try_to_date.sql on its own is
    not enough: DuckDB's string->DATE cast wants a separated date, and an
    unseparated one either errors or, worse, parses as a year. Rebuilding the
    separators first and then handing it to try_to_date keeps the
    "null rather than raise on anything unparseable" behaviour every other
    date cast in this project has.

    Empty strings, nulls and anything shorter than 8 characters come back
    null, which is what a still-Ongoing recall's `termination_date` is
    supposed to be.
#}
{% macro yyyymmdd_to_date(expr) -%}
{{ try_to_date(
    "nullif(substr(" ~ expr ~ ", 1, 4), '') || '-' || substr(" ~ expr ~ ", 5, 2) || '-' || substr(" ~ expr ~ ", 7, 2)"
) }}
{%- endmacro %}
