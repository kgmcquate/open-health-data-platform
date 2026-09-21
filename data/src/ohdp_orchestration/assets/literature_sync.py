# The @asset body's `context` param is resolved by Dagster, not annotated
# here — see openmetadata_seed_sync.py's identical header comment for why.
# no-untyped-call: metadata.client.put() (the OM SDK's low-level REST client)
# carries no type stubs — see this module's docstring for why we call it
# directly instead of the (typed) create_or_update helper.
# mypy: disable-error-code="no-untyped-def, no-untyped-call"
"""Upserts the curated literature corpus (``ohdp_ingestion.literature``,
ranked from OpenAlex + cross-checked against Europe PMC's MeSH headings, driven
by ``seed/literature_domains.yml``) into OpenMetadata's Context Center as
Knowledge Pages — abstract + citation metadata + link-out, tagged to the Domain
FQNs it matched. Full-text ingestion is out of scope for now (would need OM's
object-storage config, not set up in this deployment — revisit behind
Unpaywall/Europe PMC's OA subset if agents need full text, not just abstracts).

**Why the raw REST client, not ``metadata.create_or_update``:** the Context
Center Knowledge Page API (``PUT/POST /v1/contextCenter/pages``, confirmed live
against this deployment's own OpenAPI spec — it is a dedicated API, not the
generic ``docStore``/``CreateDocumentRequest`` surface) is new enough that the
installed SDK's (2.0.1.0) entity-to-route registry (``ometa_api.ROUTES``)
doesn't know about it yet, so the high-level ``create_or_update`` helper can't
resolve its endpoint. ``metadata.client.put(...)`` is the same call
``create_or_update`` makes internally for every entity it *does* know about
(``ometa_api.py``'s ``_create``) — this module does that one step by hand.
``PUT`` is confirmed idempotent (server-side "create or update a Knowledge
Page" semantics), so re-materializing this asset monthly upserts by name
rather than erroring or duplicating.

Uses the same ``OpenMetadata`` SDK client construction as
``openmetadata_seed_sync``/``openmetadata_dagster_sync`` — see those modules'
docstrings for why this talks to OM directly rather than through
``MetadataWorkflow``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dagster import asset
from metadata.generated.schema.api.data.createPage import CreatePage
from metadata.generated.schema.entity.data.article import Article
from metadata.generated.schema.entity.data.page import PageType
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.type.basic import (
    DateTime,
    EntityName,
    FullyQualifiedEntityName,
    Markdown,
)
from metadata.ingestion.ometa.ometa_api import OpenMetadata

from ohdp_ingestion.literature import OpenAlexClient, SelectedWork, select_literature
from ohdp_ingestion.literature.selection import load_domain_selections
from ohdp_orchestration.assets.openmetadata_sync import _openmetadata_server_config
from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_GROUP_NAME = "openmetadata_sync"
_KEY_PREFIX = "openmetadata"

_SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / "literature_domains.yml"

_CONTEXT_CENTER_PAGES_PATH = "/contextCenter/pages"


def _om_client() -> OpenMetadata[Any, Any]:
    return OpenMetadata(OpenMetadataConnection.model_validate(_openmetadata_server_config()))


def _page_name(openalex_id: str) -> str:
    """``openalex-w<id>`` — globally unique, stable across re-runs (the upsert
    key), independent of any title/author text that might itself change."""
    return f"openalex-{openalex_id.lower()}"


def _page_markdown(selected: SelectedWork) -> str:
    work = selected.work
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


def _create_page_request(selected: SelectedWork) -> CreatePage:
    work = selected.work
    return CreatePage(
        name=EntityName(_page_name(work.openalex_id)),
        displayName=work.title or work.openalex_id,
        description=Markdown(_page_markdown(selected)),
        pageType=PageType.Article,
        page=Article(
            publicationDate=(
                DateTime(datetime.combine(work.publication_date, datetime.min.time()))
                if work.publication_date
                else None
            )
        ),
        domains=[FullyQualifiedEntityName(d) for d in sorted(selected.domains)],
    )


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def literature_sync(context) -> None:
    """Selects the ranked/cross-checked literature corpus and upserts one
    Context Center Knowledge Page per paper."""
    selections = load_domain_selections(_SEED_PATH)
    with OpenAlexClient(contact_email=settings.openalex_contact_email) as openalex:
        selected = select_literature(
            selections,
            openalex=openalex,
            current_year=datetime.now(UTC).year,
            contact_email=settings.openalex_contact_email,
        )

    metadata = _om_client()
    synced = 0
    failed = 0
    for work_id, entry in selected.items():
        request = _create_page_request(entry)
        try:
            metadata.client.put(
                _CONTEXT_CENTER_PAGES_PATH,
                data=request.model_dump_json(exclude_none=True),
            )
            synced += 1
        except Exception:
            failed += 1
            context.log.exception(f"Failed to upsert literature page for {work_id}")

    context.log.info(
        f"Synced {synced} literature pages across {len(selections)} domains "
        f"({failed} failed)"
    )
