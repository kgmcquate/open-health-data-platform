#!/usr/bin/env python
"""One-time (re-runnable) scraper: a Socrata catalog -> per-dataset components.

    cd data && uv run python scripts/scrape_socrata.py --domain healthdata.gov --top-n 8
    cd data && uv run python scripts/scrape_socrata.py --domain data.cdc.gov  --top-n 8

Walks the Socrata catalog API for one domain and writes every dataset as a
Dagster **component instance** into that domain's single defs file:

    ohdp_orchestration/defs/<source>/datasets/defs.yaml

one YAML document per dataset, ``\n---\n`` between them. Dagster's component
loader reads a multi-document ``defs.yaml`` natively (``parse_yamls_with_source_position``
in ``dagster.components.core.decl``), giving each document its own component
node keyed by position, so source positions — and the ``dagster/code_references``
metadata built from them — still point at the right line. A dataset's identity
comes entirely from its ``attributes`` (``raw_table`` and the domain), never from
the path, which is what makes one file per domain equivalent to a directory per
dataset.

Each document is an instance of that domain's ``SocrataDataset`` subclass (its
``attributes`` block is the ``DatasetConfig`` contract). The ``--top-n``
most-viewed datasets are written ``enabled: true``; the rest land disabled so
the catalog is captured without every dataset scheduling itself. Also
regenerates the domain's dbt sources file.

Re-running is safe: manual edits to ``enabled``, ``cadence``, ``row_limit`` and
``incremental_cursor`` are preserved; everything else refreshes from the live
catalog. The file is rewritten whole, so datasets that left the catalog simply
stop being emitted — there is nothing to prune.

ADR-0008 (the config-driven shape), ADR-0018 (one scraper for every domain).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ohdp_ingestion.cdc import CDC
from ohdp_ingestion.healthdata_gov import HEALTHDATA_GOV
from ohdp_ingestion.naming import database as ns_database
from ohdp_ingestion.naming import schema as ns_schema
from ohdp_ingestion.socrata import DatasetConfig, SocrataDomain, iter_catalog

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = "data/scripts/scrape_socrata.py"

# Attributes a human may override in a generated file; never stomped on re-run.
_PRESERVE = ("enabled", "cadence", "row_limit", "incremental_cursor")


@dataclass(frozen=True)
class _Target:
    """Where one domain's generated files go, and what component type they are."""

    socrata: SocrataDomain

    @property
    def defs_dir(self) -> Path:
        return _REPO / f"data/src/ohdp_orchestration/defs/{self.socrata.source}"

    @property
    def datasets_dir(self) -> Path:
        return self.defs_dir / "datasets"

    @property
    def datasets_file(self) -> Path:
        return self.datasets_dir / "defs.yaml"

    @property
    def dbt_sources(self) -> Path:
        return _REPO / f"data/dbt/models/raw/_stg_{self.socrata.source}__sources.yml"

    @property
    def component_type(self) -> str:
        """Dotted path of the ``SocrataDataset`` subclass, resolved from the
        defs package rather than hard-coded per domain."""
        return f"ohdp_orchestration.defs.{self.socrata.source}.component.{self.class_name}"

    @property
    def class_name(self) -> str:
        return _CLASS_NAMES[self.socrata.source]


# The component class in `defs/<source>/component.py`. Not derivable from the
# source slug (HealthDataGov, not Healthdata_Gov), so it is stated once here.
_CLASS_NAMES = {
    HEALTHDATA_GOV.source: "HealthDataGovDataset",
    CDC.source: "CDCDataset",
}

_TARGETS = {d.domain: _Target(d) for d in (HEALTHDATA_GOV, CDC)}


def _load_existing(defs_file: Path) -> dict[str, dict[str, Any]]:
    """id -> the `attributes` block of every dataset component already on disk.

    `safe_load_all` over the one multi-document file; a first run (or a file a
    hand-edit has left unparseable) just yields no overrides to preserve.
    """
    existing: dict[str, dict[str, Any]] = {}
    if not defs_file.exists():
        return existing
    try:
        documents = list(yaml.safe_load_all(defs_file.read_text()))
    except yaml.YAMLError:
        return existing
    for data in documents:
        attrs = data.get("attributes") if isinstance(data, dict) else None
        if isinstance(attrs, dict) and attrs.get("id"):
            existing[attrs["id"]] = attrs
    return existing


def _dump_dataset(cfg: DatasetConfig, target: _Target) -> str:
    attributes = {
        "id": cfg.id,
        "name": cfg.name,
        "publisher": cfg.publisher,
        "enabled": cfg.enabled,
        "cadence": cfg.cadence,
        "raw_table": cfg.raw_table,
        "incremental_cursor": cfg.incremental_cursor,
        "row_limit": cfg.row_limit,
        "source_url": cfg.source_url,
        "page_views": cfg.page_views,
        "keywords": cfg.keywords,
        "description": cfg.description,
        "columns": [c.model_dump() for c in cfg.columns],
    }
    return yaml.safe_dump(
        {"type": target.component_type, "attributes": attributes},
        sort_keys=False,
        width=100,
        allow_unicode=True,
    )


def _write_dbt_sources(configs: list[DatasetConfig], target: _Target) -> None:
    """Raw tables, one schema per domain (ADR-0013)."""
    source = target.socrata.source
    raw_database = ns_database("raw")
    raw_schema = ns_schema("raw", source)
    enabled = [c for c in configs if c.enabled]
    doc = {
        "version": 2,
        "sources": [
            {
                "name": source,
                "description": (
                    f"{target.socrata.title} (Socrata) raw tables, "
                    f"{raw_database}.{raw_schema}. Generated by {_SCRIPT}."
                ),
                "database": raw_database,
                "schema": raw_schema,
                "tables": [
                    {
                        "name": c.raw_table,
                        "description": f"{c.name} ({c.publisher}). {c.source_url}",
                        "columns": [
                            {"name": col.name, "description": col.description}
                            for col in c.columns
                            if col.description
                        ],
                    }
                    for c in sorted(enabled, key=lambda c: c.raw_table)
                ],
            }
        ],
    }
    target.dbt_sources.parent.mkdir(parents=True, exist_ok=True)
    target.dbt_sources.write_text(
        f"# Generated by {_SCRIPT} — do not edit by hand.\n"
        + yaml.safe_dump(doc, sort_keys=False, width=100)
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--domain",
        choices=sorted(_TARGETS),
        default=HEALTHDATA_GOV.domain,
        help="Socrata domain to scrape",
    )
    ap.add_argument("--out", type=Path, default=None, help="override the datasets/defs.yaml path")
    ap.add_argument(
        "--limit", type=int, default=None, help="cap catalog entries scanned (default: all)"
    )
    ap.add_argument(
        "--top-n", type=int, default=8, help="enable this many datasets, by total page views"
    )
    ap.add_argument("--min-columns", type=int, default=2, help="skip datasets with fewer columns")
    ap.add_argument("--row-limit", type=int, default=500_000, help="row cap in every config")
    args = ap.parse_args()

    target = _TARGETS[args.domain]
    out_file: Path = args.out or target.datasets_file
    out_file.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing(out_file)

    seen: dict[str, DatasetConfig] = {}
    scanned = 0
    for dataset in iter_catalog(target.socrata, limit=args.limit):
        scanned += 1
        if len(dataset.columns) < args.min_columns or dataset.id in seen:
            continue
        seen[dataset.id] = DatasetConfig.from_catalog(dataset, row_limit=args.row_limit)

    ranked = sorted(seen.values(), key=lambda c: c.page_views, reverse=True)
    top_ids = {c.id for c in ranked[: args.top_n]}

    table_counts: dict[str, int] = {}
    documents: list[str] = []
    for cfg in ranked:
        cfg.enabled = cfg.id in top_ids

        # Two distinct datasets can share a name (re-publishes). Disambiguate the
        # raw table with the 4x4 id so nothing collides. `table_name` already put
        # the id on the end of any name it had to shorten, so only names short
        # enough to survive uncapped can still land here.
        base_table = cfg.raw_table
        table_counts[base_table] = table_counts.get(base_table, 0) + 1
        if table_counts[base_table] > 1:
            cfg.raw_table = f"{base_table}_{cfg.id.replace('-', '_')}"

        if prior := existing.get(cfg.id):
            for key in _PRESERVE:
                if key in prior:
                    setattr(cfg, key, prior[key])

        documents.append(_dump_dataset(cfg, target))

    out_file.write_text(
        f"# Generated by {_SCRIPT} — do not edit by hand.\n"
        f"# One YAML document per dataset ({len(documents)}), newest catalog scrape.\n"
        + "\n---\n".join(documents)
    )
    _write_dbt_sources(list(seen.values()), target)

    by_cadence: dict[str, int] = {}
    for cfg in seen.values():
        if cfg.enabled:
            by_cadence[cfg.cadence] = by_cadence.get(cfg.cadence, 0) + 1

    enabled_total = sum(1 for c in seen.values() if c.enabled)
    print(f"{target.socrata.domain}: scanned {scanned} catalog entries")
    print(f"wrote {len(documents)} dataset components to {out_file.relative_to(_REPO)}")
    print(f"enabled {enabled_total}: {dict(sorted(by_cadence.items()))}")
    print(f"dbt sources -> {target.dbt_sources.relative_to(_REPO)}")


if __name__ == "__main__":
    main()
