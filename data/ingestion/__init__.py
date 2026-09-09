"""Typed clients for public health data sources.

One subpackage per source. Each exposes:
  - a client class that fetches raw records over HTTP
  - a schema contract (pydantic model) the raw records are validated against
  - a `fetch()` entrypoint returning an Arrow table or a path to Parquet

Ingestion writes only to raw tables. All shaping happens in dbt.
"""
