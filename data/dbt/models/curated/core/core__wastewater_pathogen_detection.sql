-- Core layer: per-sample wastewater PCR detections for the two pathogens CDC
-- currently features on its front page -- measles and Influenza A(H5).
--
-- The two source datasets declare identical schemas (the same 38 NWSS lab
-- columns), but the columns are listed **explicitly** in both branches rather
-- than unioned with `select *`. dlt creates each raw table independently and
-- orders columns by the order it first encounters them, so two tables with
-- equal column *sets* can still have different column *order* -- and a
-- positional UNION ALL would then line measles' site up against H5's state with
-- no error at all. Naming every column makes that impossible.
--
-- `pathogen` comes from a literal rather than from `pcr_target`, which carries
-- the assay name ("mev_wt", "fluav a h5") rather than a readable pathogen.
--
-- Grain is one PCR sample, not one week: detection here is yes/no per sample,
-- and the weekly detection *rate* is computed in the mart, where the
-- denominator (samples tested) is explicit.
{{ config(materialized="table") }}

{% set sample_columns = [
    'sample_id', 'site', 'state_territory', 'counties_served', 'county_fips',
    'population_served', 'sample_collect_date', 'sample_location',
    'sample_matrix', 'pcr_target', 'pcr_target_detect', 'pcr_target_avg_conc',
    'pcr_target_units', 'lod_sewage', 'ingest_ts',
] %}

with unioned as (

    {% for label, model in [
        ('Measles',         'stg_cdc__wastewater_measles'),
        ('Influenza A(H5)', 'stg_cdc__wastewater_h5'),
    ] %}
    select
        '{{ label }}' as pathogen,
        {{ sample_columns | join(',\n        ') }}
    from {{ ref(model) }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}

)

select
    pathogen,
    sample_id,
    site                                        as site_id,
    state_territory                             as state_abbr,
    counties_served,
    county_fips,
    try_cast(population_served as bigint)       as population_served,
    {{ try_to_date('sample_collect_date') }}            as sample_collect_date,
    sample_location,
    sample_matrix,
    pcr_target                                  as pcr_assay,
    lower(pcr_target_detect) = 'yes'            as is_detected,
    try_cast(pcr_target_avg_conc as double)     as pcr_avg_concentration,
    pcr_target_units                            as pcr_units,
    try_cast(lod_sewage as double)              as limit_of_detection,
    ingest_ts
from unioned
where {{ try_to_date('sample_collect_date') }} is not null
