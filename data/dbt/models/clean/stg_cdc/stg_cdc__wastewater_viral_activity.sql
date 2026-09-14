-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/socrata_current_rows.sql; materialization is
-- the project default (table) -- see that macro for why a full rebuild.
{{ cdc_current_rows('cdc_wastewater_viral_activity_level_for_sars_cov_2_influenza_a_and_rsv') }}
