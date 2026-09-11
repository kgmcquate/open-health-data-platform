{#
    One database per medallion layer (RAW/CLEAN/CURATED, ADR-0013) via each
    model's `+database` config; falls back to target.database (dbt's own
    default behavior) when a node doesn't set one.
#}
{% macro generate_database_name(custom_database_name=none, node=none) -%}
    {%- if custom_database_name is none -%}
        {{ target.database }}
    {%- else -%}
        {{ custom_database_name | trim }}
    {%- endif -%}
{%- endmacro %}
