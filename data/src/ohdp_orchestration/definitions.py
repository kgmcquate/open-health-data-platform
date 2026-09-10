"""The single Definitions object for the code location (ARCHITECTURE.md §7).

Everything the daemon and webserver see is assembled here. Keep this file an
assembly point only — no asset bodies, no business logic.

Defs are autoloaded from the ``ohdp_orchestration.defs`` package: every Dagster
component discovered under it (currently the config-driven HealthData.gov
ingestion) is merged into one Definitions object.
"""

from __future__ import annotations

from pathlib import Path

from dagster.components import load_defs

from ohdp_orchestration import defs as _defs_module

# `load_defs` (not `load_from_defs_folder`) on purpose: the Docker image installs
# this package `--no-editable` (ADR-0004), so at runtime this file lives in
# site-packages with no project `pyproject.toml` next to it. `load_defs` only
# needs the module — but its default `project_root` autodetection still walks up
# for a pyproject and raises when there isn't one, so pass the package dir
# explicitly (it only affects code-reference links in the UI).
_project_root = Path(__file__).resolve().parent

defs = load_defs(_defs_module, project_root=_project_root)
