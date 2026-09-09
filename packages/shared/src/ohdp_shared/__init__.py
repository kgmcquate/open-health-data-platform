"""Shared building blocks for the Open Health Data Platform.

Keep this package dependency-light. Everything here is imported by both the
ingestion/orchestration side and the API side, so it must not pull in Dagster,
FastAPI, DuckDB, or any heavy runtime.
"""

from ohdp_shared.logging import configure_logging, get_logger
from ohdp_shared.settings import Settings, settings

__all__ = ["Settings", "settings", "configure_logging", "get_logger"]
