{#
    The month an OpenAQ `days/monthly` row covers, as a DATE on the first of
    that month.

    Not taken from `period_label`, despite the name: OpenAQ's `period.label`
    is the *interval* ("1 month"), not a year-month, so the label is the same
    string on every row of the table. Taking the month from it produced a
    null month_start on every row and -- worse -- made `(sensor_id,
    period_label)` a dedupe key that collapsed a sensor's whole history to
    one arbitrary month. ohdp_ingestion.openaq.source now stamps a real
    year-month onto `period_label` for new loads, but this expression stays
    the single source of truth so the two cannot drift, and so rows already
    in RAW under the old label are read correctly without a re-ingestion.

    The month is taken from the *midpoint* of the period rather than from its
    start. OpenAQ's period bounds are the station's local month expressed as
    an instant (a month running 2026-08-01T03:00Z to 2026-09-01T03:00Z is
    August for a station several hours behind UTC), and dlt lands both the
    `__utc` and `__local` variants as the same instant, so there is no local
    wall clock left to truncate. A start-of-period truncation would therefore
    put some stations in the wrong month, and would additionally depend on
    the session time zone while the raw column is still TIMESTAMPTZ. A point
    halfway through a month-long window is in that month under any offset.
#}
{% macro openaq_period_month(from_col, to_col) -%}
cast(
    date_trunc('month', {{ from_col }} + ({{ to_col }} - {{ from_col }}) / 2)
    as date
)
{%- endmacro %}
