"""Jobs. The core one is the end-to-end build: ingest -> dbt build -> publish.
Concurrency capped at 1 (ARCHITECTURE.md §3, §4)."""
