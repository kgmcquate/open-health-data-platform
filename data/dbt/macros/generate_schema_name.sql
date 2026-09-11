{#
    dbt's default macro concatenates <target_schema>_<custom_schema>
    (e.g. "CLEAN_healthdata_gov"). Every model here sets an explicit `+schema`
    naming the real source/mart schema (ADR-0013), so return it verbatim — the
    layer itself lives in the database (generate_database_name.sql).
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
