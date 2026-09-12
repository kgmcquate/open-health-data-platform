# The @asset body's `context` param is resolved by Dagster, not annotated
# here — `_validate_context_type_hint` compares the raw (string, under
# `annotations` import) annotation object-identically, so a typed annotation
# always fails.
# mypy: disable-error-code="no-untyped-def"
"""Turns every Cube measure into an OpenMetadata ``Metric`` entity
(ARCHITECTURE.md §7 `catalog/openmetadata/sync` — "Cube meta -> Metric
entities" — and the M2 build-order item, §8). Cube is the single definition
of every metric (ADR-0003); this sync is what makes that definition visible
to the discovery MCP the chatbot reads (§6), same job
``openmetadata_dagster_sync`` does for the Dagster asset catalog and
``openmetadata_sync`` does for Snowflake/dbt.

Bespoke, like ``openmetadata_dagster_sync``, not run through
``MetadataWorkflow``: ``openmetadata-ingestion==2.0.1`` ships no Cube source
connector, so there is no ``Source``/``Sink`` pipeline to configure. Instead
this reads Cube's own REST metadata endpoint
(``GET {CUBEJS_API_URL}/cubejs-api/v1/meta``, authenticated the same way any
Cube API caller is — a JWT signed with ``CUBEJS_API_SECRET``, ADR-0003 §consequences,
platform/helm/charts/cube/templates/NOTES.txt) and writes ``Metric`` entities
via OpenMetadata's SDK directly. Cube's meta endpoint deliberately never
exposes each measure's underlying SQL (`sql`/`sql_table`) over the API, so
unlike ``openmetadata_dagster_sync``'s endpoints this sync draws no lineage
edge to the Snowflake mart underneath — ``metricExpression`` is left unset
rather than faked from the measure's name.

One ``Metric`` per Cube measure, named ``<cube>__<measure>`` (Cube's own
fully-qualified ``<cube>.<measure>`` with the dot swapped for a double
underscore — ``Metric`` has no parent service, so its name doubles as its
FQN, and a literal dot there would collide with FQN part-separation). Every
dimension on a measure's own cube is attached to it as a ``MetricDimension``
— Cube has no notion of "this dimension only applies to that measure", so
all of a cube's dimensions apply to all of its measures.

The job/schedule for this asset live in ``ohdp_orchestration.jobs``/
``.schedules``, not here — this module is asset bodies only
(``ohdp_orchestration.assets``'s own convention, see its ``__init__.py``).
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
from dagster import asset
from metadata.generated.schema.api.data.createMetric import CreateMetricRequest
from metadata.generated.schema.entity.data.metric import (
    MetricDimension,
    MetricType,
)
from metadata.generated.schema.entity.data.metric import (
    Type as DimensionType,
)
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.type.basic import EntityName, Markdown
from metadata.ingestion.ometa.ometa_api import OpenMetadata

from ohdp_orchestration.assets.openmetadata_sync import _openmetadata_server_config
from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_GROUP_NAME = "openmetadata_sync"
_KEY_PREFIX = "openmetadata"

# Cube `aggType` (`semantic/cube/model/*.yml`'s measure `type:`) -> OM's
# MetricType. Cube's own "number"/"string"/... `type` field describes the
# measure's *result* type, not its aggregation, so this maps `aggType`
# specifically; anything not seen in this project's cubes yet falls back to
# OTHER rather than a hard failure.
_CUBE_AGG_TYPE_TO_METRIC_TYPE: dict[str, MetricType] = {
    "sum": MetricType.SUM,
    "avg": MetricType.AVERAGE,
    "count": MetricType.COUNT,
    "countDistinct": MetricType.COUNT,
    "countDistinctApprox": MetricType.COUNT,
    "min": MetricType.MIN,
    "max": MetricType.MAX,
    "number": MetricType.DERIVED,
}


def _cube_auth_token() -> str:
    """A Cube API JWT — Cube's own auth scheme (no claims needed for the
    default `queryRewrite`, which only reads `securityContext.tier`;
    unset here means every catalog sync runs as an untiered caller)."""
    return jwt.encode({}, settings.cube_api_secret, algorithm="HS256")


def _fetch_cube_meta() -> list[dict[str, Any]]:
    """One round-trip to Cube's metadata endpoint for every cube's measures
    and dimensions — the same endpoint the Cube Playground and MCP surface
    use, not a bespoke query."""
    response = httpx.get(
        f"{settings.cube_api_url}/cubejs-api/v1/meta",
        headers={"Authorization": _cube_auth_token()},
        timeout=30,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    cubes: list[dict[str, Any]] = payload["cubes"]
    return cubes


def _metric_dimension(dimension: dict[str, Any]) -> MetricDimension:
    short_name = str(dimension["name"]).split(".")[-1]
    return MetricDimension(
        name=short_name,
        type=DimensionType.TIME if dimension.get("type") == "time" else DimensionType.CATEGORICAL,
        description=dimension.get("description"),
    )


def _metric_request(
    measure: dict[str, Any], dimensions: list[MetricDimension]
) -> CreateMetricRequest:
    """Builds the CreateMetricRequest for one Cube measure. `measure["name"]`
    is Cube's own fully-qualified `<cube>.<measure>`; see this module's
    docstring for why the OM entity name swaps the dot for a double
    underscore."""
    cube_name, _, short_name = str(measure["name"]).partition(".")
    entity_name = f"{cube_name}__{short_name}"

    return CreateMetricRequest(
        name=EntityName(entity_name),
        displayName=measure.get("title"),
        description=Markdown(measure["description"]) if measure.get("description") else None,
        metricType=_CUBE_AGG_TYPE_TO_METRIC_TYPE.get(str(measure.get("aggType")), MetricType.OTHER),
        dimensions=dimensions or None,
    )


def _om_client() -> OpenMetadata[Any, Any]:
    return OpenMetadata(OpenMetadataConnection.model_validate(_openmetadata_server_config()))


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def openmetadata_cube_metrics_sync(context) -> None:
    """Reads every cube's measures/dimensions from Cube's metadata endpoint
    and upserts one OpenMetadata ``Metric`` entity per measure. See this
    module's docstring for why this talks to Cube's REST API and OM's SDK
    directly rather than going through ``MetadataWorkflow``."""
    context.log.info(f"Reading Cube metadata from {settings.cube_api_url}")
    cubes = _fetch_cube_meta()

    metadata = _om_client()
    synced = 0
    for cube in cubes:
        dimensions = [_metric_dimension(d) for d in cube.get("dimensions", [])]
        for measure in cube.get("measures", []):
            metadata.create_or_update(_metric_request(measure, dimensions))
            synced += 1

    context.log.info(f"Synced {synced} Cube measures into OpenMetadata Metric entities")
