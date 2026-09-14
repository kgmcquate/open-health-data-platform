{#
    One database per medallion layer (ADR-0013), each of which is its own
    Iceberg catalog now that Snowflake is the catalog (ADR-0019) and each of
    which dbt reaches through its own DuckDB `ATTACH` (profiles.yml). Applied
    from each model's `+database` config; falls back to target.database (dbt's
    own default behavior) when a node doesn't set one.

    Upper-cased for the same reason every other identifier here is: Horizon
    addresses catalogs in all capitals, and it is what Snowflake resolves an
    unquoted identifier to.
#}
{% macro generate_database_name(custom_database_name=none, node=none) -%}
    {%- if custom_database_name is none -%}
        {{ target.database }}
    {%- else -%}
        {{ custom_database_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
