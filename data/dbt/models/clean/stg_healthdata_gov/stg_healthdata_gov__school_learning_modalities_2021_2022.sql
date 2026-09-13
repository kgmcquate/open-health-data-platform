-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/healthdata_gov_current_rows.sql.
{{ config(
    materialized="incremental",
    incremental_strategy="merge",
    unique_key="socrata_id",
    on_schema_change="append_new_columns"
) }}

{{ healthdata_gov_current_rows('school_learning_modalities_2021_2022') }}
