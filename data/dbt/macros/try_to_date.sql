{#
    Snowflake's `try_to_date` — parse to a date, null rather than error on
    anything unparseable. DuckDB spells the same thing `try_cast(... as date)`,
    which also accepts a timestamp input (dlt types some Socrata
    `calendar_date` columns that way), so it covers both shapes the raw tables
    carry.
#}
{% macro try_to_date(expr) -%}
try_cast({{ expr }} as date)
{%- endmacro %}
