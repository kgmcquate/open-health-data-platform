# The @asset body's `context` param is resolved by Dagster, not annotated
# here — `_validate_context_type_hint` compares the raw (string, under
# `annotations` import) annotation object-identically, so a typed annotation
# always fails.
# mypy: disable-error-code="no-untyped-def"
"""Applies the checked-in OpenMetadata seed content — ``seed/domains.yml`` and
``seed/glossary.yml``, siblings of this package — via OpenMetadata's Python SDK. Closes M3.3's
domain/glossary half (docs/chatbot.md §M3.3): before this asset existed, `seed/` was just
checked-in documentation with nothing reading it.

Seed content lives under ``ohdp_orchestration/`` rather than ``catalog/openmetadata/`` (where
it started) so it ships inside the Dagster image for free — ``data/Dockerfile`` already copies
all of ``data/`` and installs it as the ``ohdp-data`` wheel (hatchling bundles every file under
a package directory, not just ``.py`` sources), the same way
``defs/healthdata_gov/datasets/defs.yaml`` already rides along today.

Domains are created (or updated) with ``create_or_update``, parents before children, so this
asset is safe to materialize repeatedly or after editing the YAML — it never fails on a name
that already exists, and never deletes an entity removed from the YAML (seed content is
additive; retiring a domain/term is a manual OM action).

A domain entry's optional ``website`` becomes a link in its description and the source for its
logo (``style.iconURL``, via a favicon service — see ``_favicon_url``); its optional ``schemas``
lists Snowflake DatabaseSchema FQNs to attach the domain to (``_attach_schemas``), skipping any
schema OM hasn't ingested yet rather than failing the run.

Uses the same ``OpenMetadata`` SDK client construction as
``ohdp_orchestration.assets.openmetadata_dagster_sync`` (see that module's docstring for why
this talks to OM directly rather than through ``MetadataWorkflow``: no multi-stage
extract/transform shape here, just "read some YAML, upsert some entities").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dagster import asset
from metadata.generated.schema.api.data.createGlossary import CreateGlossaryRequest
from metadata.generated.schema.api.data.createGlossaryTerm import CreateGlossaryTermRequest
from metadata.generated.schema.api.domains.createDomain import CreateDomainRequest
from metadata.generated.schema.entity.data.databaseSchema import DatabaseSchema
from metadata.generated.schema.entity.domains.domain import DomainType
from metadata.generated.schema.entity.services.connections.metadata.openMetadataConnection import (
    OpenMetadataConnection,
)
from metadata.generated.schema.type.basic import (
    EntityName,
    FullyQualifiedEntityName,
    Markdown,
)
from metadata.generated.schema.type.basic import Style as EntityStyle
from metadata.generated.schema.type.entityReference import EntityReference
from metadata.generated.schema.type.entityReferenceList import EntityReferenceList
from metadata.ingestion.ometa.ometa_api import OpenMetadata

from ohdp_orchestration.assets.openmetadata_sync import _openmetadata_server_config

_GROUP_NAME = "openmetadata_sync"
_KEY_PREFIX = "openmetadata"

_SEED_DIR = Path(__file__).resolve().parents[1] / "seed"


def _om_client() -> OpenMetadata[Any, Any]:
    return OpenMetadata(OpenMetadataConnection.model_validate(_openmetadata_server_config()))


def _favicon_url(website: str) -> str:
    """Derives a domain's logo from its website via Google's public favicon service, rather
    than hosting a logo asset per source — keyed off the same URL the description links to, so
    the two never drift apart."""
    return f"https://www.google.com/s2/favicons?sz=128&domain={urlparse(website).netloc}"


def _attach_schemas(
    metadata: OpenMetadata[Any, Any], domain_id: Any, schema_fqns: list[str]
) -> None:
    """Points each listed Snowflake schema's ``domains`` at this domain, so OM's UI can browse
    from the domain straight to its backing data. Skips (rather than fails) a schema OM hasn't
    ingested yet — ``openmetadata_snowflake_sync`` may not have run, and seed sync must stay
    safe to run any time (see module docstring)."""
    domain_ref = EntityReference(id=domain_id, type="domain")
    for fqn in schema_fqns:
        schema = metadata.get_by_name(entity=DatabaseSchema, fqn=FullyQualifiedEntityName(fqn))
        if schema is None:
            continue
        metadata.patch_domain(
            entity=DatabaseSchema, source=schema, domains=EntityReferenceList(root=[domain_ref])
        )


def _sync_domain(
    metadata: OpenMetadata[Any, Any], entry: dict[str, Any], parent: str | None = None
) -> int:
    """Upserts one domain — with its logo/website (if set) and backing schemas (if set) — then
    recurses into its ``children`` (only CDC has any today) with this domain's own name as their
    ``parent`` FQN — the CDC domain's plain (dot-free) name is also its FQN, which is all a
    child needs to nest under it."""
    website = entry.get("website")
    description = entry["description"].strip()
    if website:
        description += f"\n\nWebsite: [{website}]({website})"
    domain = metadata.create_or_update(
        CreateDomainRequest(
            name=EntityName(entry["name"]),
            domainType=DomainType(entry["domainType"]),
            description=Markdown(description),
            style=EntityStyle(iconURL=_favicon_url(website)) if website else None,
            parent=parent,
        )
    )
    if schema_fqns := entry.get("schemas"):
        _attach_schemas(metadata, domain.id, schema_fqns)
    count = 1
    for child in entry.get("children", []):
        count += _sync_domain(metadata, child, parent=entry["name"])
    return count


def _sync_domains(metadata: OpenMetadata[Any, Any]) -> int:
    spec = yaml.safe_load((_SEED_DIR / "domains.yml").read_text())["domains"]
    return sum(
        _sync_domain(metadata, entry)
        for entry in [*spec["source_aligned"], *spec["consumer_aligned"]]
    )


def _sync_glossary(metadata: OpenMetadata[Any, Any]) -> int:
    spec = yaml.safe_load((_SEED_DIR / "glossary.yml").read_text())["glossary"]
    metadata.create_or_update(
        CreateGlossaryRequest(
            name=EntityName(spec["name"]),
            description=Markdown(spec["description"].strip()),
        )
    )
    for term in spec["terms"]:
        metadata.create_or_update(
            CreateGlossaryTermRequest(
                glossary=FullyQualifiedEntityName(spec["name"]),
                name=EntityName(term["name"]),
                description=Markdown(term["description"].strip()),
            )
        )
    return len(spec["terms"])


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def openmetadata_seed_sync(context) -> None:
    """Upserts the checked-in Domain and Glossary seed content (``seed/*.yml``) into
    OpenMetadata."""
    metadata = _om_client()
    domain_count = _sync_domains(metadata)
    term_count = _sync_glossary(metadata)
    context.log.info(f"Synced {domain_count} domains and {term_count} glossary terms")
