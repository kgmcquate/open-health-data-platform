-- Clean layer: one row per `openfda.application_number` array entry on a drug
-- enforcement report, with the parent's recall_number resolved off
-- `_dlt_parent_id` -- see macros/openfda_child_rows.sql.
{{ openfda_child_rows('application_number') }}
