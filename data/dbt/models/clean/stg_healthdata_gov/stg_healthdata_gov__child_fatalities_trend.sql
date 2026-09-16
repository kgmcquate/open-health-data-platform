-- Clean layer: latest row per Socrata :id off the append-only raw table.
-- Body is shared -- see macros/healthdata_gov_current_rows.sql.
{{ healthdata_gov_current_rows('child_fatalities_trend') }}
