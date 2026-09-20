"""Curated scientific literature ingestion — ranks papers by citation popularity
(OpenAlex), cross-checks domain relevance against PubMed MeSH headings (Europe
PMC), and hands the survivors to ``ohdp_orchestration.assets.literature_sync``
for upsert into OpenMetadata's Context Center.

Distinct from ``apps/api/src/ohdp_agent/literature.py``, which does live,
citation-verified Europe PMC search at chat time (docs/chatbot.md §2.3) — this
package builds a standing, pre-ranked corpus instead. See docs/decisions and the
``literature_domains.yml`` seed for the selection rules.
"""

from ohdp_ingestion.literature.openalex import OpenAlexClient, OpenAlexError, OpenAlexWork
from ohdp_ingestion.literature.selection import DomainSelection, SelectedWork, select_literature

__all__ = [
    "DomainSelection",
    "OpenAlexClient",
    "OpenAlexError",
    "OpenAlexWork",
    "SelectedWork",
    "select_literature",
]
