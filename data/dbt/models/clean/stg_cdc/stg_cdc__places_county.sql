-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql; materialization is
-- the project default (table) -- see that macro for why a full rebuild.
{{ cdc_current_rows('places_local_data_for_better_health_county_data_2025_release') }}
