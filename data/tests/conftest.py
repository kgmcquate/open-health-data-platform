"""Ensure the dbt manifest exists before any test imports the code location.

The `lakehouse` dbt assets need `dbt/target/manifest.json` (gitignored). CI's dbt
job and the image build produce it; this makes `pytest` self-sufficient too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


def pytest_configure(config: object) -> None:
    manifest = _DBT_DIR / "target" / "manifest.json"
    if manifest.exists():
        return
    # `dbt` (not `python -m dbt.cli.main`): dbt v2/Fusion has no importable
    # `dbt.cli.main` module — it's a standalone binary (data/scripts/
    # install_dbt_fusion.sh), resolved here via the venv's `bin/dbt`, which
    # that script points at Fusion without disturbing dbt-core (still
    # installed transitively, for dagster-dbt's own Python-level imports).
    if not (_DBT_DIR / "dbt_packages").exists():
        subprocess.run(
            ["dbt", "deps"],
            cwd=_DBT_DIR,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["dbt", "parse"],
        cwd=_DBT_DIR,
        check=True,
        capture_output=True,
    )
