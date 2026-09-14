"""Offline checks for the config-driven Socrata ingestion — every domain.

Nothing here hits the network: dlt sources are lazy, so building the Definitions
object only parses the component instances and wires assets/jobs/schedules.

Each check runs once per domain (ADR-0018 made the component, the config
contract and the scraper domain-agnostic, so the checks are too). Assertions
sweep every instance in one test rather than parametrizing per dataset —
data.cdc.gov alone is ~1,000 of them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ohdp_ingestion import naming
from ohdp_ingestion.cdc import CDC
from ohdp_ingestion.healthdata_gov import HEALTHDATA_GOV
from ohdp_ingestion.socrata import CADENCES, DatasetConfig, SocrataDomain

_DEFS = Path(__file__).resolve().parents[1] / "src/ohdp_orchestration/defs"

_DOMAINS = (HEALTHDATA_GOV, CDC)
_COMPONENT_CLASS = {
    HEALTHDATA_GOV.source: "HealthDataGovDataset",
    CDC.source: "CDCDataset",
}


@pytest.fixture(params=_DOMAINS, ids=lambda d: d.source)
def socrata(request: pytest.FixtureRequest) -> SocrataDomain:
    return request.param  # type: ignore[no-any-return]


def _defs_file(socrata: SocrataDomain) -> Path:
    return _DEFS / socrata.source / "datasets" / "defs.yaml"


def _documents(socrata: SocrataDomain) -> list[tuple[str, dict]]:
    """(where, document) for every component instance in the domain's defs file.

    One multi-document `defs.yaml` per domain, `---` between instances — the
    layout Dagster's component loader reads natively. `where` is a
    `defs.yaml#<n>` label standing in for the old per-dataset path, so a failing
    assertion still says which instance it was.
    """
    path = _defs_file(socrata)
    if not path.exists():
        return []
    documents = [d for d in yaml.safe_load_all(path.read_text()) if d]
    return [(f"{path}#{i}", doc) for i, doc in enumerate(documents)]


def _configs(socrata: SocrataDomain) -> list[tuple[str, DatasetConfig]]:
    return [
        (where, DatasetConfig.model_validate(doc["attributes"]))
        for where, doc in _documents(socrata)
    ]


def _catalog_key(socrata: SocrataDomain, cfg: DatasetConfig) -> str:
    return f"sources/{socrata.source}/{cfg.raw_table}"


def _table_key(socrata: SocrataDomain, cfg: DatasetConfig) -> str:
    return f"ingestion/{socrata.source}/{cfg.raw_table}"


def _raw_key(socrata: SocrataDomain, cfg: DatasetConfig) -> str:
    database = naming.database("raw").lower()
    schema = naming.schema("raw", socrata.source).lower()
    return f"lakehouse/{database}/{schema}/{cfg.raw_table}"


def test_scraper_has_run(socrata: SocrataDomain) -> None:
    assert _documents(socrata), f"run scripts/scrape_socrata.py --domain {socrata.domain} first"


def test_every_instance_is_a_valid_dataset_component(socrata: SocrataDomain) -> None:
    want_class = _COMPONENT_CLASS[socrata.source]
    for where, doc in _documents(socrata):
        assert doc["type"].endswith(want_class), where
        cfg = DatasetConfig.model_validate(doc["attributes"])
        # A leading underscore is legal and expected: `table_name()` adds one
        # wherever the dataset name starts with a digit, matching dlt.
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", cfg.raw_table), where
        assert cfg.cadence in CADENCES, where


def test_raw_table_names_are_unique(socrata: SocrataDomain) -> None:
    tables = [c.raw_table for _, c in _configs(socrata)]
    assert len(tables) == len(set(tables))


def test_every_dataset_has_a_catalog_asset(socrata: SocrataDomain) -> None:
    from ohdp_orchestration.definitions import defs

    keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    for _, cfg in _configs(socrata):
        assert _catalog_key(socrata, cfg) in keys


def test_only_enabled_datasets_get_the_downstream_assets(socrata: SocrataDomain) -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    for _, cfg in _configs(socrata):
        catalog_str = _catalog_key(socrata, cfg)
        catalog = graph.get(by_str[catalog_str])
        assert catalog.is_materializable is False

        table_str = _table_key(socrata, cfg)
        raw_str = _raw_key(socrata, cfg)
        if cfg.enabled:
            table = graph.get(by_str[table_str])
            assert table.is_materializable is True
            assert by_str[catalog_str] in table.parent_keys
            assert by_str[table_str] in catalog.child_keys

            # The raw-layer label is a spec, not an op: it never runs, it only
            # receives the runless materialization the table asset reports.
            raw = graph.get(by_str[raw_str])
            assert raw.is_materializable is False
            assert by_str[table_str] in raw.parent_keys
        else:
            assert table_str not in by_str
            assert catalog.child_keys == set()
            # Deliberately *not* asserting `raw_str not in by_str`: the
            # `lakehouse/` raw keyspace is shared with dbt's own source nodes
            # (assets/lakehouse_dbt.py derives the same [prefix, database,
            # schema, table] key), so a disabled dataset left behind in a
            # generated `_stg_<source>__sources.yml` legitimately has a key
            # there. What matters is that nothing ingests it.


def test_catalog_assets_carry_column_schema_and_metadata(socrata: SocrataDomain) -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}
    for _, cfg in _configs(socrata):
        if not cfg.columns:
            continue
        node = graph.get(by_str[_catalog_key(socrata, cfg)])
        schema = node.metadata["dagster/column_schema"]
        assert {c.name for c in schema.columns} == {c.name for c in cfg.columns}
        assert node.metadata["socrata_id"] == cfg.id
        assert node.metadata["socrata_domain"] == socrata.domain
        assert node.tags["enabled"] == str(cfg.enabled).lower()


def test_three_cadence_jobs_and_schedules_regardless_of_enabled_set(
    socrata: SocrataDomain,
) -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    prefix = f"{socrata.source}_"
    jobs = {j.name for j in rd.get_all_jobs() if j.name.startswith(prefix)}
    assert jobs == {f"{prefix}{cadence}_ingest" for cadence in CADENCES}

    schedules = {s.name for s in rd.schedule_defs if s.name.startswith(prefix)}
    assert schedules == {f"{prefix}{cadence}_schedule" for cadence in CADENCES}

    # every enabled dataset lands in exactly its cadence job
    enabled = [c for _, c in _configs(socrata) if c.enabled]
    per_job = {
        j.name: {k.to_user_string() for k in j.asset_layer.executable_asset_keys}
        for j in rd.get_all_jobs()
        if j.name in jobs
    }
    for cadence in CADENCES:
        want = {_table_key(socrata, c) for c in enabled if c.cadence == cadence}
        assert per_job[f"{prefix}{cadence}_ingest"] == want


def test_domains_do_not_share_asset_keys() -> None:
    """Two domains can publish a dataset of the same name; the `source` segment
    of every key is what keeps them apart, so a collision here would mean one
    domain's table is silently overwriting the other's node."""
    per_domain = {
        d.source: {_catalog_key(d, c) for _, c in _configs(d)}
        | {_table_key(d, c) for _, c in _configs(d) if c.enabled}
        for d in _DOMAINS
    }
    healthdata, cdc = per_domain[HEALTHDATA_GOV.source], per_domain[CDC.source]
    assert healthdata & cdc == set()
