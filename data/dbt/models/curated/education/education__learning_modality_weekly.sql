-- Mart layer: weekly K-12 learning modality, at district x week grain.
--
-- `students_in_person` / `students_hybrid` / `students_remote` split the
-- district's student_count across three columns by the week's reported
-- modality, so a dashboard can SUM each without a per-modality filter and a
-- stacked area chart falls straight out. student_count itself stays, since
-- summing the three and summing it must agree.
{{ config(materialized="table") }}

select
    school_year,
    district_nces_id,
    district_name,
    city,
    state,
    week,
    learning_modality,
    student_count,
    operational_schools,
    case when learning_modality = 'In Person' then student_count else 0 end as students_in_person,
    case when learning_modality = 'Hybrid' then student_count else 0 end as students_hybrid,
    case when learning_modality = 'Remote' then student_count else 0 end as students_remote,
    ingest_ts
from {{ ref('core__school_learning_modality_weekly') }}
