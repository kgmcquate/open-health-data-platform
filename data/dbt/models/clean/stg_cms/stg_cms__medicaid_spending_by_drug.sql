-- Clean layer: RAW.CMS is already a full-dataset replace each run (no
-- per-row cursor to dedupe on) -- see macros/cms_current_rows.sql.
{{ cms_current_rows('medicaid_spending_by_drug') }}
