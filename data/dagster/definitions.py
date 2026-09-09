"""The single Definitions object for the code location (ARCHITECTURE.md §7).

Everything the daemon and webserver see is assembled here. Keep this file an
assembly point only — no asset bodies, no business logic.
"""

from __future__ import annotations

from dagster import Definitions

# from ohdp_orchestration.assets import air_quality, surveillance
# from ohdp_orchestration.jobs import build_job
# from ohdp_orchestration.schedules import daily_build_schedule
# from ohdp_orchestration.resources import RESOURCES

defs = Definitions(
    assets=[],
    jobs=[],
    schedules=[],
    sensors=[],
    resources={},
)
