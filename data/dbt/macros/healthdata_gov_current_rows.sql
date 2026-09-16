{#
    HealthData.gov's binding of macros/socrata_current_rows.sql -- see there
    for what the clean layer does and why.
#}
{% macro healthdata_gov_current_rows(raw_table) -%}
{{ socrata_current_rows('healthdata_gov', raw_table) }}
{%- endmacro %}
