-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql; materialization is
-- the project default (table) -- see that macro for why a full rebuild.
{{ cdc_current_rows('weekly_united_states_hospitalization_metrics_by_jurisdiction_during_mandatory_reporting_period_from_august_1_aemt_mg7g') }}
