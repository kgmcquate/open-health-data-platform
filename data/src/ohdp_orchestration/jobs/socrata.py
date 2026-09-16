"""The cadence asset job every Socrata source gets three of.

Each job selects table assets by their ``domain`` + ``cadence`` tags (set in
``ohdp_orchestration.components.socrata``) rather than by name, so it needs no
knowledge of the dataset instances — adding a dataset never touches these
modules. A cadence with nothing enabled yet is an empty job (its scheduled run
is a no-op).
"""

from __future__ import annotations

from dagster import AssetSelection, define_asset_job
from dagster._core.definitions.unresolved_asset_job_definition import (
    UnresolvedAssetJobDefinition,
)

from ohdp_ingestion.socrata import Cadence, SocrataDomain


def cadence_job(socrata: SocrataDomain, cadence: Cadence) -> UnresolvedAssetJobDefinition:
    """The ``<source>_<cadence>_ingest`` asset job."""
    selection = AssetSelection.tag("domain", socrata.source) & AssetSelection.tag(
        "cadence", cadence
    )
    return define_asset_job(
        name=f"{socrata.source}_{cadence}_ingest",
        selection=selection,
        description=f"{socrata.title} {cadence} ingestion bucket.",
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
            "domain": socrata.source,
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
