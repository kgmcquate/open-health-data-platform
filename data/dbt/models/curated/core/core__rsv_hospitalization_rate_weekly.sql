-- Core layer: RSV-NET laboratory-confirmed RSV hospitalisation rates.
--
-- This table multiplexes several things through one `data_type` column: weekly
-- and cumulative *rates* (the surveillance series), monthly rates, and a set of
-- clinical-characteristic rows ("Diabetes", "In-Hospital Death", ...) whose
-- `estimate` is a percent of hospitalised patients, not a rate. Those are
-- different units in the same column, so `estimate_type` is carried through and
-- the mart filters on it -- mixing them in one average would be meaningless.
--
-- The stratum columns use two different "all" spellings ('All' and
-- 'All Sexes' / 'All Race/Ethnicities') depending on the vintage of the row;
-- both are normalised to 'All' so a filter on the overall series is one
-- predicate rather than three.
{{ config(materialized="table") }}

select
    state                                       as state_abbr,
    season,
    data_type                                   as measure,
    rate_type,
    estimate_type,
    date_type,
    try_to_date(date)                           as period_end,
    age_category,
    case when sex   in ('All', 'All Sexes')            then 'All' else sex  end as sex,
    case when race  in ('All', 'All Race/Ethnicities') then 'All' else race end as race_ethnicity,
    try_cast(estimate as double)                as estimate,
    -- The two units live in one `estimate` column; split them so a measure can
    -- never average a rate together with a percent.
    case when estimate_type = 'Rate per 100,000' then try_cast(estimate as double) end
                                                as rate_per_100k,
    case when estimate_type = 'Percent'          then try_cast(estimate as double) end
                                                as percent_of_patients,
    ingest_ts
from {{ ref('stg_cdc__rsv_net') }}
where try_to_date(date) is not null
