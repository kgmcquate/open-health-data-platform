# The @asset body's `context` param is resolved by Dagster, not annotated
# here — `_validate_context_type_hint` compares the raw (string, under
# `annotations` import) annotation object-identically, so a typed annotation
# always fails.
# mypy: disable-error-code="no-untyped-def"
"""Publishes the Dagster **asset graph** itself into OpenMetadata — not
job/pipeline/run metadata, unlike ``openmetadata_sync``'s two other syncs
(``openmetadata_snowflake_sync``/``openmetadata_dbt_sync``), which run OM's
own ``openmetadata-ingestion`` connectors via ``MetadataWorkflow``. OM's
vendored ``openmetadata-ingestion[dagster]`` connector
(``metadata.ingestion.source.pipeline.dagster``) is job-shaped: it turns
every Dagster job into an OM ``Pipeline`` entity and every op into a
``Task``, and only touches assets as an incidental lineage bridge —
resolving each asset's declared dependencies to *pre-existing* OM ``Table``
entities and drawing a lineage edge routed through that job's ``Pipeline``
node. Two problems follow: every table pair in a job fans out through the
same job node, making lineage look messy to business users; and the
``sources/healthdata_gov/<raw_table>`` catalog assets — one unexecuted
``AssetSpec`` per scraped HealthData.gov dataset
(``defs/healthdata_gov/component.py``), created for *every* dataset whether
or not it's enabled — have no job membership and no materializations, so the
connector can't see them at all. There is no asset-catalog ingestion mode to
opt into instead (confirmed against the installed
``openmetadata-ingestion==2.0.1`` package: no
``AssetCatalog``/``DagsterAssetMetadata`` source type exists anywhere in
it), so this asset bypasses ``MetadataWorkflow`` for the Dagster side
entirely and talks to both systems directly:

* Dagster's own GraphQL API (read through ``graphql-authz-proxy`` rather
  than the raw ``dagster-webserver`` Service — same reasoning as
  ``dagster-monitoring``
  (``platform/helm/charts/dagster-monitoring/values.yaml``): a pod-to-pod
  caller with none of oauth2-proxy's ``X-Forwarded-*`` headers falls into
  the proxy's public-viewer group, so this job is bound by the same
  read-only allowlist as everyone else rather than a side channel around
  it. See ``dagster_graphql_url`` in ``ohdp_shared.settings``) — one query
  against the top-level ``assetNodes`` field, which exposes far more than
  the vendored connector's own asset query (``assetKey``, ``description``,
  ``tags``, ``metadataEntries``, ``dependedByKeys``).
* OpenMetadata's Python SDK client
  (``metadata.ingestion.ometa.ometa_api.OpenMetadata``) directly, rather
  than through a ``Source``/``Sink`` pipeline — there's no multi-stage
  extract/transform shape here, just "read the graph once, upsert some
  catalog entities."

Every ``sources/<domain>/<raw_table>`` asset becomes an ``APIEndpoint``
under a per-domain ``APIService``/``APICollection`` (today just
``healthdata_gov`` — keyed off the asset key's domain segment, not
hardcoded, so a second source domain needs no code change here), tagged with
whether it's actually ingested yet and at what cadence — every dataset any
``sources/`` domain publishes ends up browsable in OpenMetadata this way,
ingested or not. Endpoint metadata (``_endpoint_request`` below) is read
only from Dagster's *generic* asset conventions — ``description``, the
standard ``dagster/uri``/``dagster/column_schema`` metadata keys, an
``enabled`` tag — never a field only one particular source happens to
define (an upstream dataset id, say), so a non-HealthData.gov ``sources/``
domain that doesn't happen to set any of those still gets a valid (if
sparser) APIEndpoint rather than being skipped. For datasets that are
enabled, a direct
``APIEndpoint -> Table`` lineage edge is drawn into the Snowflake RAW table
``openmetadata_snowflake_sync`` already created — skipping the intermediate
``ingestion/<domain>/<raw_table>`` dlt asset, since it's plumbing with no
natural OM entity type, not something a business user needs to see. No
``Pipeline``/``Task`` entities are created by this asset at all. dbt's own
RAW→CLEAN→CURATED lineage (``openmetadata_dbt_sync``) is untouched — this
asset only fills the gap upstream of RAW.

The job/schedule for this asset live in ``ohdp_orchestration.jobs``/
``.schedules``, not here — this module is asset bodies only
(``ohdp_orchestration.assets``'s own convention, see its ``__init__.py``).
"""

from __future__ import annotations

from typing import Any

import httpx
from dagster import asset
from metadata.generated.schema.api.classification.createClassification import (
    CreateClassificationRequest,
)
from metadata.generated.schema.api.classification.createTag import CreateTagRequest
from metadata.generated.schema.api.data.createAPICollection import (
    CreateAPICollectionRequest,
)
from metadata.generated.schema.api.data.createAPIEndpoint import (
    CreateAPIEndpointRequest,
)
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.api.services.createApiService import (
    CreateApiServiceRequest,
)
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.apiService import ApiServiceType
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.type.apiSchema import APISchema
from metadata.generated.schema.type.basic import (
    EntityName,
    FullyQualifiedEntityName,
    Markdown,
)
from metadata.generated.schema.type.entityLineage import EntitiesEdge
from metadata.generated.schema.type.schema import DataTypeTopic, FieldModel, FieldName
from metadata.generated.schema.type.tagLabel import (
    LabelType,
    State,
    TagFQN,
    TagLabel,
    TagSource,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.ometa.utils import build_entity_reference, model_str
from metadata.utils import fqn
from pydantic import AnyUrl

from ohdp_orchestration.assets.openmetadata_sync import (
    _SNOWFLAKE_SERVICE_NAME,
    _openmetadata_server_config,
)
from ohdp_shared.settings import settings

# Must match `openmetadata_sync`'s own `_GROUP_NAME`/`_KEY_PREFIX` — kept as
# separate literals rather than imports since the two modules' asset bodies
# are otherwise independent (same convention as
# `defs/healthdata_gov/component.py`'s cross-module key-prefix comments).
_GROUP_NAME = "openmetadata_sync"
_KEY_PREFIX = "openmetadata"

# Dagster asset-key prefixes this sync cares about — must match
# `defs/healthdata_gov/component.py`'s `_SOURCES_PREFIX`/`_SNOWFLAKE_PREFIX`.
# `ingestion/<domain>/<table>` (the dlt hop in between) is deliberately never
# looked up by prefix here: it's plumbing with no OM entity of its own, only
# a stop on the way from `sources/` to `snowflake/`.
_SOURCES_KEY_PREFIX = "sources"
_SNOWFLAKE_KEY_PREFIX = "snowflake"

_API_COLLECTION_NAME = "datasets"

_INGESTION_CLASSIFICATION = "Ingestion"
_CADENCE_CLASSIFICATION = "Cadence"

# A `dagster/column_schema` TableColumn's `type` is free text — Dagster
# itself imposes no fixed vocabulary on it, so each `sources/` domain's own
# component picks its own spellings. This maps the common ones seen across
# sources to OM's DataTypeTopic; anything unrecognized falls back to STRING
# (never a hard failure — a column just shows up untyped in OM).
_COLUMN_TYPE_TO_OM_TYPE: dict[str, DataTypeTopic] = {
    "number": DataTypeTopic.DOUBLE,
    "float": DataTypeTopic.DOUBLE,
    "double": DataTypeTopic.DOUBLE,
    "int": DataTypeTopic.INT,
    "integer": DataTypeTopic.INT,
    "long": DataTypeTopic.LONG,
    "bool": DataTypeTopic.BOOLEAN,
    "boolean": DataTypeTopic.BOOLEAN,
    "checkbox": DataTypeTopic.BOOLEAN,
    "date": DataTypeTopic.DATE,
    "calendar_date": DataTypeTopic.DATE,
    "datetime": DataTypeTopic.TIMESTAMP,
    "timestamp": DataTypeTopic.TIMESTAMP,
    "string": DataTypeTopic.STRING,
    "text": DataTypeTopic.STRING,
}

_ASSET_NODES_QUERY = """
query OpenMetadataAssetGraph {
  assetNodes {
    assetKey { path }
    description
    tags { key value }
    dependedByKeys { path }
    metadataEntries {
      label
      __typename
      ... on TextMetadataEntry { text }
      ... on UrlMetadataEntry { url }
      ... on TableSchemaMetadataEntry {
        schema { columns { name type description } }
      }
    }
  }
}
"""


def _om_client() -> OpenMetadata[Any, Any]:
    return OpenMetadata(OpenMetadataConnection.model_validate(_openmetadata_server_config()))


def _fetch_asset_nodes() -> list[dict[str, Any]]:
    """One GraphQL round-trip for the whole asset graph — see this module's
    docstring for why this reads Dagster directly rather than through OM's
    vendored (job-shaped) Dagster connector."""
    response = httpx.post(
        f"{settings.dagster_graphql_url}/graphql",
        json={"query": _ASSET_NODES_QUERY},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(f"Dagster GraphQL query failed: {payload['errors']}")
    nodes: list[dict[str, Any]] = payload["data"]["assetNodes"]
    return nodes


def _metadata_entry_map(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["label"]: entry for entry in node["metadataEntries"]}


def _bootstrap_tags(metadata: OpenMetadata[Any, Any]) -> dict[str, TagLabel]:
    """Creates (idempotently) the small tag vocabulary applied to every
    catalog APIEndpoint below, and returns ready-to-attach TagLabels keyed
    ``"<Classification>.<Tag>"``. Reuses each classification/tag's own name
    to build the TagFQN directly (all ASCII words, no dots) rather than
    round-tripping through `metadata.utils.fqn.build` — the technique OM's
    own connectors use when a tag might already exist under an unknown FQN
    ((`metadata.utils.tag_utils.get_tag_label`), which doesn't apply here
    since we just created these ourselves this call.
    """
    metadata.create_or_update(
        CreateClassificationRequest(
            name=EntityName(_INGESTION_CLASSIFICATION),
            description=Markdown(
                "Whether a cataloged sources/ dataset is already flowing into the platform."
            ),
        )
    )
    metadata.create_or_update(
        CreateClassificationRequest(
            name=EntityName(_CADENCE_CLASSIFICATION),
            description=Markdown(
                "How often a cataloged sources/ dataset refreshes once ingested."
            ),
        )
    )

    tags: dict[str, TagLabel] = {}
    for classification, tag, description in [
        (_INGESTION_CLASSIFICATION, "Ingested", "Already landing in the Snowflake RAW layer."),
        (
            _INGESTION_CLASSIFICATION,
            "Available",
            "Cataloged from its source but not yet ingested.",
        ),
        (_CADENCE_CLASSIFICATION, "Daily", "Refreshed daily once ingested."),
        (_CADENCE_CLASSIFICATION, "Weekly", "Refreshed weekly once ingested."),
        (_CADENCE_CLASSIFICATION, "Monthly", "Refreshed monthly once ingested."),
    ]:
        metadata.create_or_update(
            CreateTagRequest(
                classification=FullyQualifiedEntityName(classification),
                name=EntityName(tag),
                description=Markdown(description),
            )
        )
        tags[f"{classification}.{tag}"] = TagLabel(
            tagFQN=TagFQN(f"{classification}.{tag}"),
            source=TagSource.Classification,
            labelType=LabelType.Automated,
            state=State.Suggested,
        )
    return tags


def _ensure_api_collection(metadata: OpenMetadata[Any, Any], domain: str) -> str:
    """Creates (idempotently) the APIService/APICollection for one
    `sources/<domain>/...` group and returns the collection's FQN. One
    collection per domain today (just ``healthdata_gov``) — keyed off the
    asset key's domain segment rather than hardcoded, so a second source
    domain needs no code change here."""
    service = metadata.create_or_update(
        CreateApiServiceRequest(name=EntityName(domain), serviceType=ApiServiceType.Rest)
    )
    collection = metadata.create_or_update(
        CreateAPICollectionRequest(
            name=EntityName(_API_COLLECTION_NAME),
            description=Markdown(
                f"Datasets cataloged from the `{domain}` source, ingested or not."
            ),
            service=FullyQualifiedEntityName(model_str(service.fullyQualifiedName)),
        )
    )
    return model_str(collection.fullyQualifiedName)


def _endpoint_request(
    node: dict[str, Any], collection_fqn: str, tags: dict[str, TagLabel]
) -> CreateAPIEndpointRequest:
    """Builds the CreateAPIEndpointRequest for one `sources/<domain>/<table>`
    catalog asset. Deliberately reads nothing but generic Dagster
    conventions, so this works for any domain's external-source catalog
    under `sources/`, not just HealthData.gov's: the asset key's own last
    segment as the endpoint name, its `description`, and Dagster's
    *standard* metadata keys `dagster/uri` (``SYSTEM_METADATA_KEY_URI``) and
    `dagster/column_schema` (``SYSTEM_METADATA_KEY_COLUMN_SCHEMA``) if the
    asset happens to set them — never a field only one particular source
    happens to define (an upstream dataset id, say). The `cadence` metadata
    entry / `enabled` tag
    `defs/healthdata_gov/component.py` happens to set are read the same
    way — present or not, with a safe default either way, never required.
    Dataset's human title (e.g. "Perpetrators Trend") isn't captured in the
    asset graph separately from its description for HealthData.gov today —
    `_catalog_spec()` sets Dagster's `description` field to
    ``self.description or self.name`` with nothing else to fall back on — so
    `displayName` is left unset here and the OM UI falls back to `name`
    (the asset key's last segment)."""
    raw_table = node["assetKey"]["path"][-1]
    entries = _metadata_entry_map(node)
    node_tags = {t["key"]: t["value"] for t in node["tags"]}

    cadence = entries.get("cadence", {}).get("text", "weekly").capitalize()
    enabled = node_tags.get("enabled") == "true"
    uri = entries.get("dagster/uri", {}).get("url")

    schema_fields = None
    table_schema = entries.get("dagster/column_schema", {}).get("schema")
    if table_schema:
        schema_fields = [
            FieldModel(
                name=FieldName(column["name"]),
                dataType=_COLUMN_TYPE_TO_OM_TYPE.get(
                    str(column["type"]).lower(), DataTypeTopic.STRING
                ),
                dataTypeDisplay=column["type"],
                description=(
                    Markdown(column["description"]) if column.get("description") else None
                ),
            )
            for column in table_schema["columns"]
        ]

    return CreateAPIEndpointRequest(
        name=EntityName(raw_table),
        description=Markdown(node["description"]) if node.get("description") else None,
        apiCollection=FullyQualifiedEntityName(collection_fqn),
        endpointURL=AnyUrl(uri) if uri else None,
        responseSchema=APISchema(schemaFields=schema_fields) if schema_fields else None,
        tags=[
            tags[f"{_CADENCE_CLASSIFICATION}.{cadence}"],
            tags[f"{_INGESTION_CLASSIFICATION}.{'Ingested' if enabled else 'Available'}"],
        ],
    )


def _find_downstream_snowflake_key(
    node: dict[str, Any], nodes_by_key: dict[tuple[str, ...], dict[str, Any]]
) -> tuple[str, ...] | None:
    """Breadth-first walk of `dependedByKeys` (Dagster's *downstream*
    dependency edges) from a `sources/<domain>/<table>` asset until it
    reaches a `snowflake/<database>/<schema>/<table>` key — passing straight
    through the `ingestion/<domain>/<table>` dlt asset in between without
    ever looking at its own key, since that hop gets no OM entity of its
    own (see module docstring)."""
    queue = list(node["dependedByKeys"])
    seen: set[tuple[str, ...]] = set()
    while queue:
        path = tuple(queue.pop(0)["path"])
        if path in seen:
            continue
        seen.add(path)
        if path[0] == _SNOWFLAKE_KEY_PREFIX:
            return path
        next_node = nodes_by_key.get(path)
        if next_node:
            queue.extend(next_node["dependedByKeys"])
    return None


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def openmetadata_dagster_sync(context) -> None:
    """Publishes the Dagster asset graph's ``sources/<domain>/*`` catalog —
    every scraped HealthData.gov dataset, ingested or not — into OpenMetadata
    as APIEndpoints under a per-domain APIService, and draws direct
    APIEndpoint -> Table lineage into the Snowflake RAW layer for datasets
    that are actually enabled. See this module's docstring for why this
    reads Dagster's GraphQL API and writes via OpenMetadata's SDK directly
    rather than going through ``MetadataWorkflow``."""
    context.log.info(f"Reading the Dagster asset graph from {settings.dagster_graphql_url}")
    nodes = _fetch_asset_nodes()
    nodes_by_key = {tuple(node["assetKey"]["path"]): node for node in nodes}
    source_nodes = [node for node in nodes if node["assetKey"]["path"][0] == _SOURCES_KEY_PREFIX]

    metadata = _om_client()
    tags = _bootstrap_tags(metadata)
    collection_fqn_by_domain = {
        domain: _ensure_api_collection(metadata, domain)
        for domain in {node["assetKey"]["path"][1] for node in source_nodes}
    }

    linked = 0
    for node in source_nodes:
        domain = node["assetKey"]["path"][1]
        request = _endpoint_request(node, collection_fqn_by_domain[domain], tags)
        endpoint = metadata.create_or_update(request)

        snowflake_key = _find_downstream_snowflake_key(node, nodes_by_key)
        if not snowflake_key:
            continue
        table_fqn = fqn.build(
            metadata=metadata,
            entity_type=Table,
            service_name=_SNOWFLAKE_SERVICE_NAME,
            database_name=snowflake_key[1],
            schema_name=snowflake_key[2],
            table_name=snowflake_key[3],
        )
        if not table_fqn:
            continue
        table_entity = metadata.get_by_name(entity=Table, fqn=table_fqn)
        if not table_entity:
            context.log.debug(
                f"{snowflake_key} not ingested into OpenMetadata yet, skipping lineage"
            )
            continue
        metadata.add_lineage(
            AddLineageRequest(
                edge=EntitiesEdge(
                    fromEntity=build_entity_reference(endpoint),
                    toEntity=build_entity_reference(table_entity),
                )
            )
        )
        linked += 1

    context.log.info(
        f"Synced {len(source_nodes)} HealthData.gov catalog datasets "
        f"({linked} linked to Snowflake RAW tables)"
    )
