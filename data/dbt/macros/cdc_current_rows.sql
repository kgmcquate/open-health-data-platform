{#
    data.cdc.gov's binding of macros/socrata_current_rows.sql -- see there for
    what the clean layer does and why.
#}
{% macro cdc_current_rows(raw_table) -%}
{{ socrata_current_rows('cdc', raw_table) }}
{%- endmacro %}
