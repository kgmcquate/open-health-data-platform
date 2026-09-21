"""Scientific literature ingestion, split by purpose across two orchestration
assets:

* ``ohdp_orchestration.assets.literature_dataset_sync`` — per-dataset OpenAlex
  *relevance* search, one dataset at a time, upserted into OpenMetadata's
  Context Center and linked by lineage to the dataset's raw-layer Table. Uses
  ``OpenAlexClient.search_relevant_works`` directly; does not use this
  package's ``selection`` module.
* ``ohdp_orchestration.assets.literature_trending_sync`` — per-Domain OpenAlex
  *recent-citation* ranking (this package's ``select_trending_literature``,
  driven by ``literature_domains.yml``), cross-checked against Europe PMC's
  MeSH headings, feeding the public Literature page's trending feed.

Distinct from ``apps/api/src/ohdp_agent/literature.py``, which does live,
citation-verified Europe PMC search at chat time (docs/chatbot.md §2.3) — both
assets here build standing, pre-computed corpora instead.
"""

from ohdp_ingestion.literature.openalex import OpenAlexClient, OpenAlexError, OpenAlexWork
from ohdp_ingestion.literature.selection import (
    DomainSelection,
    SelectedWork,
    select_trending_literature,
)

__all__ = [
    "DomainSelection",
    "OpenAlexClient",
    "OpenAlexError",
    "OpenAlexWork",
    "SelectedWork",
    "select_trending_literature",
]
