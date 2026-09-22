-- Clean layer: RAW.OPENAQ.MONTHLY_MEASUREMENTS is already a full-dataset
-- replace each run (no per-row cursor to dedupe on) -- see
-- macros/openaq_current_rows.sql. dlt flattens the API's nested `parameter`/
-- `period`/`summary`/`coverage` objects into `__`-suffixed columns on this
-- same table (they're JSON objects, not arrays, so no child tables) -- the
-- macro's dynamic column list picks those up with no per-column code here.
{{ openaq_current_rows('monthly_measurements') }}
