-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/healthdata_gov_current_rows.sql.
{{ healthdata_gov_current_rows('perpetrators_by_relationship_to_their_victims') }}
