{#
    Pairs with generate_schema_name.sql: a model named per the dbt-labs
    staging convention `stg_<source>__<table>` gets the `stg_<source>__`
    prefix stripped from its physical table name too, since that source is
    now carried by the namespace instead.

    Upper-cased for the same reason the namespace is (ADR-0019): Horizon
    addresses tables in all capitals, and it is what Snowflake resolves an
    unquoted identifier to for Cube.
#}
{% macro generate_alias_name(custom_alias_name=none, node=none) -%}
    {%- set parts = node.name.split('__', 1) -%}
    {%- if parts | length == 2 -%}
        {{ parts[1] | upper }}
    {%- elif custom_alias_name is none -%}
        {{ node.name | upper }}
    {%- else -%}
        {{ custom_alias_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
