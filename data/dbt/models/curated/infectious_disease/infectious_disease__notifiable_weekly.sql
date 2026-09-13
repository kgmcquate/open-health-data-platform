-- Mart layer: Project Tycho notifiable-disease surveillance, ready to chart.
-- One row per disease x location x epi week, state-level locations only.
--
-- Restricted to location_type STATE: the source mixes state and city rows in
-- one table, and summing across both double-counts every city inside its own
-- state. City-level analysis should read core__notifiable_disease_weekly.
{{ config(materialized="table") }}

select
    disease,
    state,
    location,
    epi_year,
    epi_week_of_year,
    epi_week_start_date,
    cases,
    incidence_per_100000
from {{ ref('core__notifiable_disease_weekly') }}
where upper(location_type) = 'STATE'
  and state is not null
