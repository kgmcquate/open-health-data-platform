-- Clean layer: RAW.OPENAQ.LOCATIONS is already a full-dataset replace each
-- run (no per-row cursor to dedupe on) -- see macros/openaq_current_rows.sql.
{{ openaq_current_rows('locations') }}
