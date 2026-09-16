-- Core layer: the CPS front-door funnel for one state, one row per state.
--
-- Joins three NCANDS cross-sections that share a state grain and the same most
-- recent federal fiscal year: referrals screened in or out, what the resulting
-- investigations concluded, and fatalities by which NCANDS file reported them.
--
-- `fiscal_year` was meant to come from the dispositions file's `year` column --
-- Socrata's own metadata documents one (added 2021-11-29, see
-- ohdp_orchestration/defs/healthdata_gov/datasets/defs.yaml) -- but the
-- currently ingested RAW.HEALTHDATA_GOV.CHILDREN_BY_DISPOSITION rows don't
-- actually carry it (dbt1308 binder error, no `year` in that relation), so
-- it's left null here rather than guessed at. Investigate on the loader side
-- if a real fiscal year is needed.
{{ config(materialized="table") }}

-- Wrapped so row_sk hashes this mart's own output names -- macros/row_sk.sql.
with mart as (

with referrals as (
    select
        state,
        total_referrals::int as total_referrals,
        total_referrals_rate_per::float as referral_rate_per_100k,
        screened_in_referrals_reports::int as screened_in_referrals,
        screened_in_referrals_reports_1::float as screened_in_pct,
        screened_out_referrals::int as screened_out_referrals,
        screened_out_referrals_percent::float as screened_out_pct,
        ingest_ts
    from {{ ref('stg_healthdata_gov__screened_referrals') }}
),

dispositions as (
    select
        state,
        cast(null as int) as fiscal_year,
        total_children::int as children_with_disposition,
        substantiated::int as substantiated,
        indicated::int as indicated,
        alternative_response::int as alternative_response,
        unsubstantiated::int as unsubstantiated,
        intentionally_false::int as intentionally_false,
        no_alleged_maltreatment::int as no_alleged_maltreatment,
        closed_with_no_finding::int as closed_with_no_finding,
        other::int as other_disposition,
        unknown::int as unknown_disposition,
        ingest_ts
    from {{ ref('stg_healthdata_gov__children_by_disposition') }}
),

fatalities as (
    select
        state,
        total_child_fatalities::int as total_child_fatalities,
        child_fatalities_reported::int as fatalities_reported_child_file,
        child_fatalities_reported_agency::int as fatalities_reported_agency_file,
        child_fatality_rates_per::float as fatality_rate_per_100k,
        ingest_ts
    from {{ ref('stg_healthdata_gov__child_fatalities_by_submission_type') }}
),

-- Spine of every state appearing in any of the three sources, then a LEFT JOIN
-- each. Chaining `full outer join` off `referrals` would drop states: with
-- `r.state` null, `r.state = f.state` never matches and that state's fatality
-- figures vanish.
spine as (
    select state from referrals
    union
    select state from dispositions
    union
    select state from fatalities
)

select
    s.state::varchar as state,
    lower(s.state) = 'national' as is_national,
    d.fiscal_year,
    r.total_referrals,
    r.referral_rate_per_100k,
    r.screened_in_referrals,
    r.screened_in_pct,
    r.screened_out_referrals,
    r.screened_out_pct,
    d.children_with_disposition,
    d.substantiated,
    d.indicated,
    d.alternative_response,
    d.unsubstantiated,
    d.intentionally_false,
    d.no_alleged_maltreatment,
    d.closed_with_no_finding,
    d.other_disposition,
    d.unknown_disposition,
    f.total_child_fatalities,
    f.fatalities_reported_child_file,
    f.fatalities_reported_agency_file,
    f.fatality_rate_per_100k,
    greatest(r.ingest_ts, d.ingest_ts, f.ingest_ts) as ingest_ts
from spine s
left join referrals r on s.state = r.state
left join dispositions d on s.state = d.state
left join fatalities f on s.state = f.state
where s.state is not null

)

select
    {{ row_sk(['state']) }} as row_sk,
    *
from mart
