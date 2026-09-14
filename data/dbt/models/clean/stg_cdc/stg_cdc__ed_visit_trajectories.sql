-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql.
{{ config(
    materialized="incremental",
    incremental_strategy="merge",
    unique_key="socrata_id",
    on_schema_change="append_new_columns"
) }}

{{ cdc_current_rows('nssp_emergency_department_visit_trajectories_by_state_and_sub_state_regions_covid_19_flu_rsv_combined') }}
