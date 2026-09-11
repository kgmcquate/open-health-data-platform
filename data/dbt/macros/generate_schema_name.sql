{#
    dbt's default macro concatenates <target_schema>_<custom_schema>
    (e.g. "OHDP_clean_healthdata_gov"). Every model here sets an explicit
    `+schema` naming the real medallion schema (ADR-0012), so return it
    verbatim on both the Snowflake and DuckDB targets.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
