-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql.
{{ config(
    materialized="incremental",
    incremental_strategy="merge",
    unique_key="socrata_id",
    on_schema_change="append_new_columns"
) }}

{{ cdc_current_rows('influenza_vaccination_coverage_for_all_ages_6_months') }}
