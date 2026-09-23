"""Walk CMS's Project Open Data catalog (``data.cms.gov/data.json``).

CMS is *not* Socrata or CKAN (ADR-0026 corrects ADR-0018's guess) — it
publishes its own ``data-api/v1`` REST API, discoverable through the federal
Project Open Data / DCAT-US catalog every ``.gov`` agency is required to
publish at ``/data.json`` (the same metadata schema Socrata's own
``Common-Core_*`` catalog fields are drawn from, which is why the cadence
mapping below is identical to :mod:`ohdp_ingestion.socrata.catalog`'s).

One catalog entry is a dataset *series* — e.g. "Medicare Part D Spending by
Drug" — republished on a cadence, each vintage its own ``distribution`` with
its own UUID. Only the ``distribution`` tagged ``"description": "latest"``
(and of ``"format": "API"``) is live data; older vintages are archival CSVs
and are skipped entirely. About 130 of CMS's ~160 series currently have one;
the rest publish CSV-only and are skipped (nothing to ingest via the API).

The ``data.json`` entry carries no column schema, so the per-dataset column
list is assembled from two further CMS surfaces:

* ``data-api/v1/dataset/{uuid}/data-viewer?size=1`` — its ``meta`` block
  carries ``headers`` (the column names, in file order), the backing CSV's
  ``csvColumnTypes``, *and* ``total_rows``, so this one request replaces the
  separate ``data-viewer/stats`` call rather than adding to it;
* the **data dictionary**, which is where the human-written column
  descriptions live. It is a Drupal node on the same site, reachable through
  ``data.cms.gov/jsonapi`` — the catalog's ``describedBy`` is the dictionary
  page's path alias whenever CMS published one as a page. Roughly 107 of the
  131 live series have one; the rest describe themselves in a PDF or XLSX
  only, and get column names and types without descriptions.

Reference: https://data.cms.gov/api-docs, https://resources.data.gov/schemas/dcat-us/v1.1/
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ohdp_ingestion.base import http_client
from ohdp_shared import get_logger

log = get_logger(__name__)

CMS_BASE_URL = "https://data.cms.gov"
_CATALOG_PATH = "/data.json"
_DATA_VIEWER_PATH = "/data-api/v1/dataset/{uuid}/data-viewer"
_DICTIONARY_LIST_PATH = "/jsonapi/node/data_dictionary_page"
_DICTIONARY_NODE_PATH = "/jsonapi/node/data_dictionary_page/{node_id}"

# Same ISO-8601 recurring-interval vocabulary Socrata's Common-Core_Update-
# Frequency uses (both are Project Open Data / DCAT-US), so the mapping to our
# three schedule buckets is identical — see
# ohdp_ingestion.socrata.catalog._CADENCE_BY_FREQUENCY for the same table.
_CADENCE_BY_FREQUENCY = {
    "R/PT1H": "daily",
    "R/PT1S": "daily",
    "R/P1D": "daily",
    "R/P2D": "daily",
    "R/P3.5D": "daily",
    "R/P0.33W": "daily",
    "R/P0.5W": "weekly",
    "R/P1W": "weekly",
    "R/P2W": "weekly",
    "R/P7D": "weekly",
    "R/P0.5M": "weekly",
    "R/P1M": "monthly",
    "R/P3M": "monthly",
    "R/P4M": "monthly",
    "R/P6M": "monthly",
    "R/P1Y": "monthly",
    "R/P2Y": "monthly",
    "R/P4Y": "monthly",
}
_DEFAULT_CADENCE = "weekly"

_UUID_RE = re.compile(r"/dataset/([0-9a-f-]{36})/data")


@dataclass(slots=True)
class CatalogColumn:
    """One column of a dataset's latest distribution.

    ``type`` is the backing CSV's advertised type (``TEXT``/``NUMERIC``/
    ``DATE``), lowercased — what CMS *says* the column holds, not what lands
    in the raw table, which dlt writes as text either way.
    """

    name: str
    type: str = ""
    description: str = ""


@dataclass(slots=True)
class CatalogDataset:
    """One CMS dataset series, normalized down to what a config needs."""

    id: str
    """UUID of the series' *latest* API distribution — stable across
    republishes of the same series (a new vintage gets a new UUID, but the
    ``data.json`` entry's ``identifier`` always points at whichever one is
    current)."""

    name: str
    description: str
    publisher: str
    landing_page: str
    modified: str | None
    update_frequency: str | None
    keywords: list[str] = field(default_factory=list)
    row_count: int = 0
    described_by: str = ""
    """The catalog's ``describedBy`` — a data dictionary *page* for most
    series, a PDF/XLSX download for the rest (which carries no columns)."""

    columns: list[CatalogColumn] = field(default_factory=list)

    @property
    def cadence(self) -> str:
        return _CADENCE_BY_FREQUENCY.get(self.update_frequency or "", _DEFAULT_CADENCE)


def _latest_api_distribution(entry: dict[str, Any]) -> dict[str, Any] | None:
    for dist in entry.get("distribution") or []:
        if dist.get("format") == "API" and dist.get("description") == "latest":
            return dist
    return None


def _uuid_from_access_url(access_url: str) -> str | None:
    match = _UUID_RE.search(access_url)
    return match.group(1) if match else None


def _dataset_details(client: Any, uuid: str) -> tuple[int, list[CatalogColumn]]:
    """Best-effort row count and column list from one ``data-viewer`` page.

    Asking for a single row is the cheapest way to get the ``meta`` block,
    which is the part we want. Both results are descriptive only — the row
    count feeds README tables and the initial enabled set, the columns feed
    docs — so a failure here is never fatal to the scrape.
    """
    try:
        resp = client.get(_DATA_VIEWER_PATH.format(uuid=uuid), params={"size": 1})
        resp.raise_for_status()
        meta = resp.json().get("meta") or {}
    except Exception:  # noqa: BLE001 — informational only
        log.warning("cms: could not fetch dataset details for %s", uuid)
        return 0, []

    types = (meta.get("data_file_meta_data") or {}).get("csvColumnTypes") or {}
    columns = [
        CatalogColumn(name=header, type=str(types.get(header) or "").lower())
        for header in (meta.get("headers") or [])
        if isinstance(header, str)
    ]
    return int(meta.get("total_rows") or 0), columns


def _squash(text: str) -> str:
    """Alphanumerics only, lower case — enough to match two spellings of the
    same title past CMS's inconsistent dashes and ampersands."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@dataclass(slots=True)
class _DictionaryIndex:
    """Every data dictionary page CMS publishes, keyed both ways we can find one."""

    by_alias: dict[str, str] = field(default_factory=dict)
    by_title: dict[str, str] = field(default_factory=dict)

    def node_id(self, described_by: str, dataset_title: str) -> str | None:
        """``describedBy`` first; it is the link CMS itself drew. Some series
        point it at the PDF edition of a dictionary they also publish as a
        page, so fall back to the page whose title is the dataset's plus
        "Data Dictionary" — which is how CMS names all of them."""
        by_alias = self.by_alias.get(urlparse(described_by).path)
        if by_alias:
            return by_alias
        return self.by_title.get(_squash(f"{dataset_title} Data Dictionary"))


def _dictionary_index(client: Any) -> _DictionaryIndex:
    """List every data dictionary page, in one paginated pass (~4 requests for
    ~175 pages) rather than a lookup per dataset: ``jsonapi`` rejects a filter
    on ``path.alias`` outright, so the match has to happen client side."""
    index = _DictionaryIndex()
    url: str | None = _DICTIONARY_LIST_PATH
    params: dict[str, Any] | None = {
        "page[limit]": 50,
        "fields[node--data_dictionary_page]": "title,path",
    }
    while url:
        try:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            page = resp.json()
        except Exception:  # noqa: BLE001 — documentation only
            log.warning("cms: could not list data dictionary pages")
            return index
        for node in page.get("data") or []:
            node_id = node.get("id")
            attrs = node.get("attributes") or {}
            if not node_id:
                continue
            alias = (attrs.get("path") or {}).get("alias")
            if alias:
                index.by_alias[alias] = node_id
            title = attrs.get("title")
            if title:
                index.by_title[_squash(title)] = node_id
        # `next` is a fully-formed absolute URL with its own query string.
        url = ((page.get("links") or {}).get("next") or {}).get("href")
        params = None
    return index


def _strip_html(markup: str | None) -> str:
    """Dictionary definitions are stored as rich text; we want a YAML line."""
    text = re.sub(r"<[^>]+>", " ", markup or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _dictionary_terms(client: Any, node_id: str) -> dict[str, str]:
    """Lower-cased column short name -> its definition, for one dictionary page."""
    try:
        resp = client.get(
            _DICTIONARY_NODE_PATH.format(node_id=node_id),
            params={"include": "field_dictionary_terms"},
        )
        resp.raise_for_status()
        included = resp.json().get("included") or []
    except Exception:  # noqa: BLE001 — documentation only
        log.warning("cms: could not fetch data dictionary %s", node_id)
        return {}

    terms: dict[str, str] = {}
    for term in included:
        attrs = term.get("attributes") or {}
        short_name = (attrs.get("field_dictionary_term_short_name") or "").strip()
        if not short_name:
            continue
        definition = attrs.get("field_dictionary_term_definition") or {}
        terms[short_name.lower()] = _strip_html(definition.get("processed"))
    return terms


def iter_catalog(
    *, with_details: bool = True, with_column_docs: bool = True, limit: int | None = None
) -> Iterator[CatalogDataset]:
    """Yield every CMS dataset series that has a live API distribution.

    ``with_details`` fetches each dataset's ``data-viewer`` meta block (one
    extra request per dataset) to populate ``row_count`` and ``columns``; set
    it to ``False`` for a fast dry run, and both come back empty.
    ``with_column_docs`` additionally resolves each dataset's data dictionary
    page (one listing pass plus one request per dataset that has one) to
    describe those columns; it does nothing without ``with_details``, since
    there are no columns to attach descriptions to.
    """
    with http_client(CMS_BASE_URL) as client:
        resp = client.get(_CATALOG_PATH)
        resp.raise_for_status()
        entries = resp.json().get("dataset", [])

        document_columns = with_details and with_column_docs
        dictionaries = _dictionary_index(client) if document_columns else _DictionaryIndex()

        yielded = 0
        for entry in entries:
            dist = _latest_api_distribution(entry)
            if dist is None:
                continue
            uuid = _uuid_from_access_url(dist.get("accessURL", ""))
            if not uuid:
                continue

            publisher = (entry.get("publisher") or {}).get("name", "")
            described_by = entry.get("describedBy") or ""
            row_count, columns = _dataset_details(client, uuid) if with_details else (0, [])

            title = entry.get("title") or uuid
            node_id = dictionaries.node_id(described_by, title) if document_columns else None
            if node_id and columns:
                terms = _dictionary_terms(client, node_id)
                for column in columns:
                    column.description = terms.get(column.name.lower(), "")

            yield CatalogDataset(
                id=uuid,
                name=title,
                description=entry.get("description") or "",
                publisher=publisher,
                landing_page=entry.get("landingPage") or f"{CMS_BASE_URL}/dataset/{uuid}",
                modified=dist.get("modified") or entry.get("modified"),
                update_frequency=entry.get("accrualPeriodicity"),
                keywords=list(entry.get("keyword") or [])[:12],
                row_count=row_count,
                described_by=described_by,
                columns=columns,
            )
            yielded += 1
            if limit is not None and yielded >= limit:
                return
