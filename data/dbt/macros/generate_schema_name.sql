{#
    dbt's default macro concatenates <target_schema>_<custom_schema>
    (e.g. "CLEAN_healthdata_gov"). Every model here sets an explicit `+schema`
    naming the real source/mart schema (ADR-0013), so return it verbatim on
    both the Snowflake and DuckDB targets — the layer itself lives in the
    database (generate_database_name.sql) on Snowflake, and collapses to this
    same schema name in DuckDB's one local catalog.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
