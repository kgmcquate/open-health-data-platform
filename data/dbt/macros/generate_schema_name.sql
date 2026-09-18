{#
    The namespace within a layer's catalog. The layer itself lives in the
    database, which comes from `+catalog_name` resolving against
    data/dbt/catalogs.yml, so the schema only has to carry the source or
    mart — ADR-0013's scheme, which ADR-0019 keeps: a Snowflake database is
    an Iceberg catalog and its schemas are that catalog's namespaces, so
    nothing has to be flattened together.

    Derived from the model name rather than configured per folder, so a model
    following the dbt-labs `stg_<source>__<table>` convention lands in the
    right namespace without a `+schema` config and without renaming the file
    (which would break its unique dbt model ID / ref()s):

      stg_<source>__<table>  ->  STG_<SOURCE>   (in CLEAN)
      core__<table>          ->  CORE           (in CURATED)
      <mart>__<table>        ->  <MART>         (in CURATED)

    So clean/stg_cdc/stg_cdc__nndss_weekly.sql lands in CLEAN.STG_CDC.

    **Upper case, deliberately.** Snowflake requires an external engine
    reaching it through the Horizon REST catalog to address namespaces and
    tables in all capitals, and unquoted SQL identifiers fold to upper case
    anyway — so this is the one casing that resolves from DuckDB and from Cube
    without quoting.

    Anything not following the `<prefix>__<name>` convention falls back to the
    explicit `+schema` config, then target.schema, same as dbt's default.

    **This macro is the only definition of the namespace layout** (ADR-0020).
    Whatever it returns, dbt creates: before building, dbt issues
    `CREATE SCHEMA IF NOT EXISTS <database>.<schema>` for every schema in the
    run, which the duckdb adapter sends to Horizon as a CREATE NAMESPACE on the
    attached Iceberg catalog. So a new mart needs no Terraform run — the folder and the
    model name are the whole change. Terraform grants `CREATE SCHEMA` on each
    layer database (platform/terraform/snowflake.tf) and stops there.

    The namespace inherits CATALOG and EXTERNAL_VOLUME from its database, since
    Iceberg tables resolve both through table -> schema -> database — which is
    why creating one here still lands the files in our own S3 bucket and not in
    Snowflake's storage.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set parts = node.name.split('__', 1) -%}
    {%- if parts | length == 2 -%}
        {{ parts[0] | upper }}
    {%- elif custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim | upper }}
    {%- endif -%}
{%- endmacro %}
