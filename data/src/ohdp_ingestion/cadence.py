"""The three ingestion schedule buckets every source's cadence jobs pick from.

Not source-specific — originally lived in :mod:`ohdp_ingestion.socrata.config`,
pulled out here once CMS needed the same three buckets without depending on
the Socrata package (ADR-0026).
"""

from __future__ import annotations

from typing import Literal

Cadence = Literal["daily", "weekly", "monthly"]
CADENCES: tuple[Cadence, ...] = ("daily", "weekly", "monthly")
