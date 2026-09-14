{#
    Snowflake's `div0` with no DuckDB equivalent: division that yields 0
    rather than an error/infinity when the divisor is 0. A null numerator or
    divisor still yields null, same as Snowflake.
#}
{% macro div0(numerator, denominator) -%}
case when ({{ denominator }}) = 0 then 0 else ({{ numerator }}) / ({{ denominator }}) end
{%- endmacro %}
