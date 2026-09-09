"""Software-defined assets, grouped by domain (air_quality, surveillance, ...).

Publish gate lives downstream: a failed dbt test must block the R2 upload
(ARCHITECTURE.md §3). Do not emit snapshot-publish as a side effect of an asr
materialization that ran before tests.
"""
