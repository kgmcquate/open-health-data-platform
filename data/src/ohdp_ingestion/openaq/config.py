"""The per-dataset config contract for OpenAQ ingestion.

One YAML file lives at ``ohdp_orchestration/defs/openaq/datasets/defs.yaml``,
hand-maintained (not scraped — see the package docstring for why) and read by
the Dagster component (``ohdp_orchestration.defs.openaq.component.OpenAQDataset``).

No ``id``/``incremental_cursor``/``ColumnSpec`` here, unlike Socrata: a
"dataset" isn't a catalog entry OpenAQ publishes, it's just which of OpenAQ's
REST resources this instance pulls — see ``ohdp_ingestion.cms.config`` for the
same reasoning applied to CMS.

Two shapes share this one schema (``resource`` is the discriminator) rather
than getting their own ``Component`` subclasses: both produce the identical
source/ingestion/lakehouse-raw asset triplet
(``ohdp_orchestration.defs.openaq.component``), so the only real difference is
which ``ohdp_ingestion.openaq.source`` function builds the ``dlt`` source and
which of the fields below it reads — not worth a second component class for.

* ``locations`` — one global, constantly-updated snapshot, no filters.
* ``monthly_measurements`` — a per-sensor fan-out (``countries``/
  ``parameters``/``reference_monitors_only`` below all apply only here; see
  that function's docstring for why each one is load-bearing for API-call
  cost, not just scope).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ohdp_ingestion.cadence import CADENCES, Cadence

__all__ = ["CADENCES", "Cadence", "DatasetConfig"]

_CRITERIA_POLLUTANTS = ("pm25", "pm10", "o3", "no2", "so2", "co")


class DatasetConfig(BaseModel):
    """A single OpenAQ REST resource to ingest, e.g. ``/v3/locations``."""

    model_config = {"extra": "forbid"}

    name: str
    publisher: str = ""
    description: str = ""
    source_url: str = ""

    cadence: Cadence = "weekly"
    enabled: bool = False

    raw_table: str = Field(description="Table name written in RAW.OPENAQ")
    row_limit: int | None = Field(default=None, description="Safety cap on rows pulled per run")

    resource: Literal["locations", "monthly_measurements"] = "locations"

    # --- monthly_measurements only ------------------------------------------
    countries: list[str] = Field(
        default_factory=lambda: ["US"],
        description="ISO 3166-1 alpha-2 country codes to fan out into (monthly_measurements only)",
    )
    parameters: list[str] = Field(
        default_factory=lambda: list(_CRITERIA_POLLUTANTS),
        description="OpenAQ parameter names to keep, e.g. 'pm25' (monthly_measurements only)",
    )
    reference_monitors_only: bool = Field(
        default=True,
        description=(
            "Restrict to locations OpenAQ flags isMonitor=true (government reference-grade "
            "stations) rather than every low-cost sensor too — see source.py's docstring for "
            "why this is a cost control, not just a data-quality preference."
        ),
    )
