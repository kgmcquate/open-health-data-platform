-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql.
{{ config(
    materialized="incremental",
    incremental_strategy="merge",
    unique_key="socrata_id",
    on_schema_change="append_new_columns"
) }}

{{ cdc_current_rows('weekly_united_states_hospitalization_metrics_by_jurisdiction_during_mandatory_reporting_period_from_august_1_2020_to_april_30_2024_and_for_data_reported_voluntarily_beginning_may_1_2024_national_healthcare_safety_network_nhsn_archived') }}
