-- Clean layer: RAW.OPENAQ.MONTHLY_MEASUREMENTS is appended over a trailing
-- lookback window each run (ohdp_ingestion.openaq.source's
-- `lookback_months`), so a revised month can show up as more than one row --
-- dedupe to the latest load per (sensor_id, period_label) in
-- macros/openaq_current_rows.sql. dlt flattens the API's nested `parameter`/
-- `period`/`summary`/`coverage` objects into `__`-suffixed columns on this
-- same table (they're JSON objects, not arrays, so no child tables) -- the
-- macro's dynamic column list picks those up with no per-column code here.
{{ openaq_current_rows('monthly_measurements', dedupe_by=['sensor_id', 'period_label']) }}
