# The @asset body's `context` param is resolved by Dagster, not annotated
# here — see openmetadata_seed_sync.py's identical header comment for why.
# no-untyped-call: metadata.client.put() (the OM SDK's low-level REST client)
# carries no type stubs — see this module's docstring for why we call it
# directly instead of the (typed) create_or_update helper.
# mypy: disable-error-code="no-untyped-def, no-untyped-call"
"""Per-dataset literature relevance sync — replaces the earlier domain-wide,
citation-popularity ``literature_sync`` (see git history / ADR if one was
written for the removal). That version ranked ~15 OpenAlex subfields by
citation count and tagged the survivors to curated Domains; the corpus was
almost never the paper a question about a *specific* dataset actually needed
(top-cited-in-a-subfield skews toward decades-old methods papers, not
"relevant to this table"), and nothing in the catalog ever linked a paper back
to the data it was about.

This asset instead walks every ``sources/<domain>/<raw_table>`` catalog asset
that is actually **enabled** (ingested into the lakehouse — see
``ohdp_orchestration.assets.openmetadata_dagster_sync``, whose ``sources/``
walk and ``_find_downstream_snowflake_key`` helper this reuses directly rather
than re-deriving the Dagster asset graph or re-guessing Snowflake's identifier
casing), runs one OpenAlex **relevance** search (``search_relevant_works`` —
free-text, OpenAlex's own relevance ranking, not citation count) per dataset
using that dataset's own ``description`` as the query, and upserts the top
matches as Context Center Knowledge Pages — same page shape
(``CreatePage``/``pageType=Article``) and same raw-REST-client rationale as
the old asset (the Knowledge Page API predates this SDK's route registry; see
below). Each page is then linked to the dataset's raw-layer ``Table`` entity
with an explicit lineage edge, the same mechanism
``openmetadata_dagster_sync`` uses for its ``APIEndpoint -> Table`` edges —
that edge, not a Domain tag, is this asset's join: browsing a table's Lineage
tab in OM now surfaces the literature that motivated/relates to it, and vice
versa. No Domain tagging is attempted here — a per-dataset relevance match has
no reliable health-topic signal to tag with, and reintroducing one would mean
reintroducing the hand-curated subfield mapping this asset replaces.

**Why the raw REST client, not ``metadata.create_or_update``:** unchanged from
the previous asset — the Context Center Knowledge Page API
(``PUT/POST /v1/contextCenter/pages``) is new enough that installed SDK
2.0.1.0's entity-to-route registry doesn't know about it. ``PUT`` is
idempotent server-side "create or update," so re-materializing this
asset upserts by name rather than duplicating.

**Lineage from a Page entity is exercised here for the first time.**
``OMetaLineageMixin.add_lineage`` posts a generic ``EntitiesEdge`` of
``EntityReference(id, type)`` pairs straight to ``/lineage`` — it does not
consult the SDK's route registry the way ``get_by_name``/``create_or_update``
do, so the same registry gap that forces the raw PUT above does not block the
lineage call. The edge's ``type`` is set to the literal string ``"page"``
(OM's own entity-type-name convention — lower-cased class name, same as
``"table"``, ``"domain"``) rather than resolved through the SDK, since
``Page`` isn't in the registry either. **Not confirmed against the live
deployment** (unlike this file's sibling modules, which mostly are) — if
OM's backend rejects a Page-typed lineage node, the edge fails and is logged,
but the page itself still gets created and synced count still counts it.

Uses the same ``OpenMetadata`` SDK client construction as
``openmetadata_seed_sync``/``openmetadata_dagster_sync`` — see those modules'
docstrings for why this talks to OM directly rather than through
``MetadataWorkflow``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from dagster import asset
from metadata.generated.schema.api.data.createPage import CreatePage
from metadata.generated.schema.api.lineage.addLineage import AddLineageRequest
from metadata.generated.schema.entity.data.article import Article
from metadata.generated.schema.entity.data.page import PageType
from metadata.generated.schema.entity.data.table import Table
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.type.basic import DateTime, EntityName, Markdown, Uuid
from metadata.generated.schema.type.entityLineage import EntitiesEdge
from metadata.generated.schema.type.entityReference import EntityReference
from metadata.ingestion.ometa.ometa_api import OpenMetadata
from metadata.ingestion.ometa.utils import build_entity_reference
from metadata.utils import fqn

from ohdp_ingestion.literature import OpenAlexClient, OpenAlexError, OpenAlexWork
from ohdp_orchestration.assets.openmetadata_dagster_sync import (
    _fetch_asset_nodes,
    _find_downstream_snowflake_key,
)
from ohdp_orchestration.assets.openmetadata_sync import (
    _SNOWFLAKE_SERVICE_NAME,
    _openmetadata_server_config,
)
from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_GROUP_NAME = "openmetadata_sync"
_KEY_PREFIX = "openmetadata"

_SOURCES_KEY_PREFIX = "sources"
_CONTEXT_CENTER_PAGES_PATH = "/contextCenter/pages"

# Per dataset, not per domain — a broad subfield search can return hundreds of
# plausible matches, but a single dataset's relevant literature is a short
# list. Keeps OpenAlex traffic and OM writes proportional to the ~40 datasets
# actually ingested today, not the >1000 cataloged-but-disabled rows.
_MAX_WORKS_PER_DATASET = 5


def _om_client() -> OpenMetadata[Any, Any]:
    return OpenMetadata(OpenMetadataConnection.model_validate(_openmetadata_server_config()))


def _page_name(openalex_id: str) -> str:
    """``openalex-w<id>`` — globally unique, stable across re-runs (the upsert
    key), independent of any title/author text that might itself change."""
    return f"openalex-{openalex_id.lower()}"


def _page_markdown(work: OpenAlexWork) -> str:
    lines = [f"# {work.title}" if work.title else "# (untitled)"]
    meta_bits = [b for b in (work.authors, work.venue, str(work.publication_year or "")) if b]
    if meta_bits:
        lines.append(" — ".join(meta_bits))
    lines.append(f"**Citations:** {work.cited_by_count}")
    links = [f"[Source]({work.url})"]
    if work.pmid:
        links.append(f"[PubMed](https://pubmed.ncbi.nlm.nih.gov/{work.pmid})")
    if work.pmcid:
        links.append(f"[PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/{work.pmcid})")
    lines.append(" · ".join(links))
    if work.abstract:
        lines.append("")
        lines.append(work.abstract)
    return "\n\n".join(lines)


def _publication_date(publication_date: date | None) -> DateTime | None:
    """Serialize an OpenAlex date-only publication date as UTC midnight — see
    the previous asset's identical helper for why (OM's Java ``Date``
    deserializer rejects a naive ``datetime``)."""
    if publication_date is None:
        return None
    return DateTime(datetime.combine(publication_date, datetime.min.time(), tzinfo=UTC))


def _create_page_request(work: OpenAlexWork) -> CreatePage:
    return CreatePage(
        name=EntityName(_page_name(work.openalex_id)),
        displayName=work.title or work.openalex_id,
        description=Markdown(_page_markdown(work)),
        pageType=PageType.Article,
        page=Article(publicationDate=_publication_date(work.publication_date)),
    )


def _tag(node: dict[str, Any], key: str) -> str | None:
    for entry in node.get("tags", []):
        if entry.get("key") == key:
            value = entry.get("value")
            return str(value) if value is not None else None
    return None


def _resolve_table_fqn(
    metadata: OpenMetadata[Any, Any],
    node: dict[str, Any],
    nodes_by_key: dict[tuple[str, ...], dict[str, Any]],
) -> str | None:
    """The raw-layer ``Table``'s FQN for an enabled ``sources/`` node, found by
    walking the live Dagster graph (``_find_downstream_snowflake_key``) rather
    than re-deriving ``RAW.<SOURCE>.<raw_table>`` by hand — that avoids any
    risk of disagreeing with Snowflake's own identifier casing (ADR-0013's
    upper-casing), which is exactly the risk ``openmetadata_dagster_sync``
    already solved once."""
    snowflake_key = _find_downstream_snowflake_key(node, nodes_by_key)
    if not snowflake_key:
        return None
    table_fqn = fqn.build(
        metadata=metadata,
        entity_type=Table,
        service_name=_SNOWFLAKE_SERVICE_NAME,
        database_name=snowflake_key[1],
        schema_name=snowflake_key[2],
        table_name=snowflake_key[3],
    )
    return str(table_fqn) if table_fqn else None


def _relevance_query(node: dict[str, Any]) -> str:
    """The free-text query for a dataset's literature search — its own
    ``description`` (``SocrataDataset._catalog_spec()`` sets this to the
    dataset's real description, or its name when the source publishes none),
    the same *generic* Dagster field ``openmetadata_dagster_sync`` reads for
    the same reason: works for any ``sources/`` domain without a per-source
    code change, not just today's CDC/HealthData.gov/CMS."""
    description = node.get("description") or ""
    return description.strip() or node["assetKey"]["path"][-1]


def _link_page_to_table(
    metadata: OpenMetadata[Any, Any], page_response: dict[str, Any], table_fqn: str
) -> bool:
    page_id = page_response.get("id")
    if not page_id:
        return False
    table_entity = metadata.get_by_name(entity=Table, fqn=table_fqn)
    if table_entity is None:
        return False
    metadata.add_lineage(
        AddLineageRequest(
            edge=EntitiesEdge(
                fromEntity=EntityReference(id=Uuid(page_id), type="page"),
                toEntity=build_entity_reference(table_entity),
            )
        )
    )
    return True


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def literature_dataset_sync(context) -> None:
    """For every ingested (``enabled: true``) ``sources/`` dataset, runs an
    OpenAlex relevance search on its own description and upserts the top
    matches as Context Center Knowledge Pages, each linked by lineage to the
    dataset's raw-layer Table entity."""
    nodes = _fetch_asset_nodes()
    nodes_by_key = {tuple(node["assetKey"]["path"]): node for node in nodes}
    source_nodes = [
        node
        for node in nodes
        if node["assetKey"]["path"][0] == _SOURCES_KEY_PREFIX and _tag(node, "enabled") == "true"
    ]

    metadata = _om_client()
    synced = 0
    linked = 0
    failed = 0
    datasets_with_table = 0
    with OpenAlexClient(contact_email=settings.openalex_contact_email) as openalex:
        for node in source_nodes:
            table_fqn = _resolve_table_fqn(metadata, node, nodes_by_key)
            if table_fqn is None:
                context.log.debug(
                    f"{node['assetKey']['path']} has no ingested raw-layer table yet, skipping"
                )
                continue
            datasets_with_table += 1

            try:
                works = openalex.search_relevant_works(
                    query=_relevance_query(node), limit=_MAX_WORKS_PER_DATASET
                )
            except OpenAlexError:
                failed += 1
                context.log.exception(f"OpenAlex search failed for {node['assetKey']['path']}")
                continue

            for work in works:
                request = _create_page_request(work)
                try:
                    response = metadata.client.put(
                        _CONTEXT_CENTER_PAGES_PATH,
                        data=request.model_dump_json(exclude_none=True),
                    )
                    synced += 1
                except Exception:
                    failed += 1
                    context.log.exception(
                        f"Failed to upsert literature page for {work.openalex_id}"
                    )
                    continue

                try:
                    if _link_page_to_table(metadata, response or {}, table_fqn):
                        linked += 1
                except Exception:
                    context.log.exception(
                        f"Failed to link literature page {work.openalex_id} to {table_fqn}"
                    )

    context.log.info(
        f"Synced {synced} dataset-linked literature pages across "
        f"{datasets_with_table}/{len(source_nodes)} ingested datasets "
        f"({linked} linked to their table, {failed} failed)"
    )
