{#
    Pairs with generate_schema_name.sql: a model named per the dbt-labs
    staging convention `stg_<source>__<table>` gets the `stg_<source>__`
    prefix stripped from its physical table name too, since that source is
    now carried by the schema instead. Everything else keeps dbt's default
    behavior (custom_alias_name if set, else the model name verbatim).
#}
{% macro generate_alias_name(custom_alias_name=none, node=none) -%}
    {%- set parts = node.name.split('__', 1) -%}
    {%- if parts | length == 2 -%}
        {{ parts[1] }}
    {%- elif custom_alias_name is none -%}
        {{ node.name }}
    {%- else -%}
        {{ custom_alias_name | trim }}
    {%- endif -%}
{%- endmacro %}
