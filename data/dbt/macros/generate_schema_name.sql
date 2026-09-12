{#
    dbt's default macro concatenates <target_schema>_<custom_schema>
    (e.g. "CLEAN_healthdata_gov"). Instead, a model named per the dbt-labs
    staging convention `stg_<source>__<table>` gets its schema inferred
    straight from that name (`stg_<source>`) — the layer already lives in the
    database (generate_database_name.sql), so the schema only needs to carry
    the source. This lets clean/healthdata_gov/stg_healthdata_gov__foo.sql
    land in CLEAN.stg_healthdata_gov without a `+schema` config and without
    renaming the file (which would break its unique dbt model ID / ref()s).
    Anything not following that convention (core, marts) falls back to the
    explicit `+schema` config, then target.schema, same as dbt's default.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set parts = node.name.split('__', 1) -%}
    {%- if parts | length == 2 -%}
        {{ parts[0] }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
