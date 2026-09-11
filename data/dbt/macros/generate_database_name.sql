{#
    Snowflake gets one database per medallion layer (RAW/CLEAN/CURATED,
    ADR-0013) via each model's `+database` config. DuckDB (local/CI) is a
    single-file catalog with no such split — applying `+database` there
    would just rename the one catalog per model and break cross-model refs,
    so this macro only takes effect on the snowflake target; DuckDB always
    resolves to target.database, same as dbt's own default behavior with no
    override.
#}
{% macro generate_database_name(custom_database_name=none, node=none) -%}
    {%- if custom_database_name is none or target.type != 'snowflake' -%}
        {{ target.database }}
    {%- else -%}
        {{ custom_database_name | trim }}
    {%- endif -%}
{%- endmacro %}
