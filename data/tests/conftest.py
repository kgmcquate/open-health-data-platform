"""Ensure the dbt manifest exists before any test imports the code location.

The `snowflake` dbt assets need `dbt/target/manifest.json` (gitignored). CI's dbt
job and the image build produce it; this makes `pytest` self-sufficient too.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"


def pytest_configure(config: object) -> None:
    manifest = _DBT_DIR / "target" / "manifest.json"
    if manifest.exists():
        return
    if not (_DBT_DIR / "dbt_packages").exists():
        subprocess.run(
            [sys.executable, "-m", "dbt.cli.main", "deps"],
            cwd=_DBT_DIR,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse"],
        cwd=_DBT_DIR,
        check=True,
        capture_output=True,
    )
