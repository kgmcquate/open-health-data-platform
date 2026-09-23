-- Clean layer: RAW.OPENFDA.DRUG_ENFORCEMENT is appended to over a trailing
-- 90-day window each run (`incremental_lag_days`), so a report re-fetched by
-- two runs lands twice -- dedupe to the latest load per recall_number in
-- macros/openfda_current_rows.sql.
{{ openfda_current_rows('drug_enforcement') }}
