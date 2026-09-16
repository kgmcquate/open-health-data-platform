-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/healthdata_gov_current_rows.sql.
{{ healthdata_gov_current_rows('covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw') }}
