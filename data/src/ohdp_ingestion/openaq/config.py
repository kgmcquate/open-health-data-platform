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
    lookback_months: int | None = Field(
        default=3,
        description=(
            "Trailing months of `days/monthly` history to (re)fetch per run, landed as "
            "write_disposition=append (monthly_measurements only). None means a full "
            "historical backfill landed as replace instead — see source.py's docstring."
        ),
    )
    max_locations: int | None = Field(
        default=None,
        description=(
            "Hard cap on locations walked *per country*, applied after the `monitor`/`iso` "
            "filters (monthly_measurements only). Unlike `row_limit` — a cap on rows, which "
            "only stops the fan-out once the calls have already been made — this is a cap on "
            "the fan-out itself, and it is the only knob that bounds the locations->sensors "
            "discovery walk. Locations come back ordered by `id` asc, so the same stations are "
            "kept run over run rather than an arbitrary subset each time."
        ),
    )
    max_sensors: int | None = Field(
        default=None,
        description=(
            "Hard cap on sensors fanned out into `days/monthly` *per country*, counted after "
            "the `parameters` filter (monthly_measurements only). Same reasoning as "
            "`max_locations`: bounds calls, not rows."
        ),
    )
    max_consecutive_errors: int = Field(
        default=25,
        description=(
            "How many sensors in a row may fail their `days/monthly` call (after dlt's own "
            "5xx/429 retries) before the run is aborted. A single sensor OpenAQ persistently "
            "500s on is skipped rather than discarding the whole run's work; a long unbroken "
            "streak means OpenAQ itself is down, and that should still fail loudly."
        ),
    )
