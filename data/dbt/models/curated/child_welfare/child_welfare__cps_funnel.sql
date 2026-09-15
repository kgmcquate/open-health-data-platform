-- Mart layer: the child protective services front door, one row per state for
-- the most recent NCANDS federal fiscal year. States only.
--
-- State grain, so every count here sums correctly across states -- the reason
-- these columns are not carried on child_welfare__maltreatment_profile.
{{ config(materialized="table") }}

select
    state,
    fiscal_year,
    total_referrals,
    referral_rate_per_100k,
    screened_in_referrals,
    screened_out_referrals,
    screened_in_pct,
    children_with_disposition,
    substantiated,
    indicated,
    alternative_response,
    unsubstantiated,
    total_victims,
    total_child_fatalities,
    fatality_rate_per_100k,
    -- Screen-in and substantiation shares recomputed from this state's own
    -- counts rather than taken from the source's rounded percentages, so the
    -- two ratios are consistent with each other and with the counts beside them.
    screened_in_referrals / nullif(total_referrals, 0) as screen_in_rate,
    substantiated / nullif(children_with_disposition, 0) as substantiation_rate,
    greatest(f.ingest_ts, v.ingest_ts) as ingest_ts
from {{ ref('core__cps_referral_funnel_state') }} f
left join (
    select
        state as victim_state,
        max(total_victims) as total_victims,
        max(ingest_ts) as ingest_ts
    from {{ ref('core__child_maltreatment_type_state') }}
    where not is_national
    group by 1
) v on f.state = v.victim_state
where not f.is_national
