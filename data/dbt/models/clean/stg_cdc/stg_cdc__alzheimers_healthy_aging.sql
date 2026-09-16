-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql; materialization is
-- the project default (table) -- see that macro for why a full rebuild.
{{ cdc_current_rows('alzheimer_s_disease_and_healthy_aging_data') }}
