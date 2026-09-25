-- Mart layer: FDA recalls counted by the month they were initiated -- the
-- time series behind the product_safety cube's trend.
--
-- Grain: product_type x classification x status x recall_initiation_month.
--
-- Two counts, because they answer different questions (see
-- product_safety__recalls): recall_count is products recalled, event_count is
-- distinct recall *actions*. event_count is only additive within a row --
-- one event can span classifications and months, so summing event_count
-- across rows over-counts. recall_count sums freely.
--
-- `status` is a *current* status, not a historical one: openFDA publishes
-- where the recall stands today, and the trailing 90-day re-merge window is
-- the only thing that updates it (see macros/openfda_current_rows.sql). So a
-- row here reads "recalls initiated in month M that are, as of the last
-- load, in status S" -- not "recalls that entered status S in month M".
--
-- Months with a null recall_initiation_date are dropped: a recall with no
-- initiation date has no place on a time axis, and it is a handful of rows.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

select
    product_type,
    recall_initiation_month,
    classification,
    classification_rank,
    status,
    count(*)                                            as recall_count,
    count(distinct event_id)                            as event_count,
    count(distinct recalling_firm)                      as firm_count,
    count(*) filter (where is_nationwide)                as nationwide_recall_count,
    count(*) filter (where is_open)                      as open_recall_count,
    avg(days_to_termination)                            as avg_days_to_termination,
    max(ingest_ts)                                      as ingest_ts
from {{ ref('core__product_recall') }}
where recall_initiation_date is not null
group by 1, 2, 3, 4, 5

)

select
    {{ row_sk(['product_type', 'recall_initiation_month', 'classification', 'status']) }} as row_sk,
    *
from mart
