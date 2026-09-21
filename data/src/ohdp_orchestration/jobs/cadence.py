"""The cadence asset job every ingestion source gets three of.

Each job selects table assets by their ``domain`` + ``cadence`` tags (set in
``ohdp_orchestration.components.socrata`` / ``ohdp_orchestration.defs.cms.component``)
rather than by name, so it needs no knowledge of the dataset instances —
adding a dataset never touches these modules. A cadence with nothing enabled
yet is an empty job (its scheduled run is a no-op).

Originally lived as ``jobs.socrata.cadence_job``, taking a ``SocrataDomain``
directly — pulled out here (ADR-0026) once CMS needed the identical job
shape without depending on the Socrata package for it. What the function
actually used was always just ``source``/``title``, so the signature is that
now.
"""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job
from dagster._core.definitions.unresolved_asset_job_definition import (
    UnresolvedAssetJobDefinition,
)

from ohdp_ingestion.cadence import Cadence


def cadence_job(source: str, title: str, cadence: Cadence) -> UnresolvedAssetJobDefinition:
    """The ``<source>_<cadence>_ingest`` asset job."""
    selection = AssetSelection.tag("domain", source) & AssetSelection.tag("cadence", cadence)
    return define_asset_job(
        name=f"{source}_{cadence}_ingest",
        selection=selection,
        description=f"{title} {cadence} ingestion bucket.",
        config={
            "execution": {
                "config": {
                    "multiprocess": {
                        "max_concurrent": 1,
                    }
                }
            }
        },
        tags={
            "domain": source,
            "cadence": cadence,
            "dagster-k8s/config": {
                "container_config": {
                    "resources": {
                        "requests": {"memory": "2Gi"},
                        "limits": {"memory": "6Gi"},
                    }
                }
            },
        },
    )
