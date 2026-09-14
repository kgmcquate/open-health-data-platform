{#
    One Iceberg catalog holds every layer, so the namespace (a Glue database;
    a schema to DuckDB and Snowflake) is what carries the medallion layer —
    `raw_<source>`, `clean_<source>`, `core`, `mart_<name>` (ADR-0019). Same
    scheme as ohdp_ingestion.naming.schema(), which the raw loader uses.

    dbt's default macro concatenates <target_schema>_<custom_schema>
    (e.g. "core_cdc"), which is not it. Derive from the model name instead:

      stg_<source>__<table>  ->  clean_<source>   (the dbt-labs staging convention)
      core__<table>          ->  core
      <mart>__<table>        ->  mart_<mart>

    So clean/cdc/stg_cdc__nndss_weekly.sql lands in lakehouse.clean_cdc without
    a `+schema` config and without renaming the file (which would break its
    unique dbt model ID / ref()s). Anything not following the `<prefix>__<name>`
    convention falls back to the explicit `+schema` config, then
    target.schema, same as dbt's default.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set parts = node.name.split('__', 1) -%}
    {%- if parts | length == 2 -%}
        {%- set prefix = parts[0] -%}
        {%- if prefix.startswith('stg_') -%}
            clean_{{ prefix[4:] }}
        {%- elif prefix == 'core' -%}
            core
        {%- else -%}
            mart_{{ prefix }}
        {%- endif -%}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
