"""Offline checks for the config-driven openFDA ingestion.

Nothing here hits the network: dlt sources are lazy, so building the
Definitions object only parses the component instances and wires
assets/jobs/schedules. Mirrors ``test_openaq_components.py``'s checks — same
reasoning: openFDA's ``datasets/defs.yaml`` is hand-maintained, not generated
(see ``ohdp_orchestration.defs.openfda``'s docstring).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ohdp_ingestion import naming
from ohdp_ingestion.openfda import CADENCES, DatasetConfig
from ohdp_ingestion.openfda.source import _build_resource_config

SOURCE = "openfda"
_DEFS_FILE = (
    Path(__file__).resolve().parents[1] / "src/ohdp_orchestration/defs/openfda/datasets/defs.yaml"
)


def _documents() -> list[tuple[str, dict[str, Any]]]:
    if not _DEFS_FILE.exists():
        return []
    documents = [d for d in yaml.safe_load_all(_DEFS_FILE.read_text()) if d]
    return [(f"{_DEFS_FILE}#{i}", doc) for i, doc in enumerate(documents)]


def _configs() -> list[tuple[str, DatasetConfig]]:
    return [(where, DatasetConfig.model_validate(doc["attributes"])) for where, doc in _documents()]


def _catalog_key(cfg: DatasetConfig) -> str:
    return f"sources/{SOURCE}/{cfg.raw_table}"


def _table_key(cfg: DatasetConfig) -> str:
    return f"ingestion/{SOURCE}/{cfg.raw_table}"


def _raw_key(cfg: DatasetConfig) -> str:
    database = naming.database("raw").lower()
    schema = naming.schema("raw", SOURCE).lower()
    return f"lakehouse/{database}/{schema}/{cfg.raw_table}"


def test_defs_file_has_at_least_one_instance() -> None:
    assert _documents(), "datasets/defs.yaml is hand-maintained -- see defs.openfda's docstring"


def test_every_instance_is_a_valid_dataset_component() -> None:
    for where, doc in _documents():
        assert doc["type"].endswith("OpenFDADataset"), where
        cfg = DatasetConfig.model_validate(doc["attributes"])
        assert re.fullmatch(r"[a-z_][a-z0-9_]*", cfg.raw_table), where
        assert cfg.cadence in CADENCES, where
        assert cfg.endpoint, where
        assert bool(cfg.incremental_cursor) == bool(cfg.primary_key), where


def test_raw_table_names_are_unique() -> None:
    tables = [c.raw_table for _, c in _configs()]
    assert len(tables) == len(set(tables))


def test_endpoints_are_unique() -> None:
    endpoints = [(c.endpoint, c.search) for _, c in _configs()]
    assert len(endpoints) == len(set(endpoints))


def test_every_dataset_has_a_catalog_asset() -> None:
    from ohdp_orchestration.definitions import defs

    keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    for _, cfg in _configs():
        assert _catalog_key(cfg) in keys


def test_only_enabled_datasets_get_the_downstream_assets() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    for _, cfg in _configs():
        catalog_str = _catalog_key(cfg)
        catalog = graph.get(by_str[catalog_str])
        assert catalog.is_materializable is False

        table_str = _table_key(cfg)
        raw_str = _raw_key(cfg)
        if cfg.enabled:
            table = graph.get(by_str[table_str])
            assert table.is_materializable is True
            assert by_str[catalog_str] in table.parent_keys
            assert by_str[table_str] in catalog.child_keys

            raw = graph.get(by_str[raw_str])
            assert raw.is_materializable is False
            assert by_str[table_str] in raw.parent_keys
        else:
            assert table_str not in by_str
            assert catalog.child_keys == set()


def test_catalog_assets_carry_metadata() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}
    for _, cfg in _configs():
        node = graph.get(by_str[_catalog_key(cfg)])
        assert node.tags["enabled"] == str(cfg.enabled).lower()


def test_three_cadence_jobs_and_schedules_regardless_of_enabled_set() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    prefix = f"{SOURCE}_"
    jobs = {j.name for j in rd.get_all_jobs() if j.name.startswith(prefix)}
    assert jobs == {f"{prefix}{cadence}_ingest" for cadence in CADENCES}

    schedules = {s.name for s in rd.schedule_defs if s.name.startswith(prefix)}
    assert schedules == {f"{prefix}{cadence}_schedule" for cadence in CADENCES}

    enabled = [c for _, c in _configs() if c.enabled]
    per_job = {
        j.name: {k.to_user_string() for k in j.asset_layer.executable_asset_keys}
        for j in rd.get_all_jobs()
        if j.name in jobs
    }
    for cadence in CADENCES:
        want = {_table_key(c) for c in enabled if c.cadence == cadence}
        assert per_job[f"{prefix}{cadence}_ingest"] == want


def test_at_least_one_dataset_is_enabled() -> None:
    assert any(cfg.enabled for _, cfg in _configs())


def test_incremental_cursor_and_primary_key_require_each_other() -> None:
    base = {"name": "x", "raw_table": "x", "endpoint": "drug/enforcement"}
    incremental = {"incremental_cursor": "report_date", "primary_key": "recall_number"}
    DatasetConfig.model_validate({**base, **incremental})
    DatasetConfig.model_validate(base)
    with pytest.raises(ValueError):
        DatasetConfig.model_validate({**base, "incremental_cursor": "report_date"})
    with pytest.raises(ValueError):
        DatasetConfig.model_validate({**base, "primary_key": "recall_number"})


def test_resource_config_without_incremental_cursor_is_a_full_replace() -> None:
    resource = _build_resource_config(
        "drug/enforcement",
        "drug_enforcement",
        api_key="",
        search='country:"United States"',
        page=1000,
        incremental_cursor=None,
        primary_key=None,
        incremental_lag_days=90,
    )
    assert resource["write_disposition"] == "replace"
    assert "primary_key" not in resource
    assert "incremental" not in resource["endpoint"]
    assert resource["endpoint"]["params"]["search"] == 'country:"United States"'
    assert "sort" not in resource["endpoint"]["params"]


def test_resource_config_with_incremental_cursor_is_a_lag_windowed_merge() -> None:
    resource = _build_resource_config(
        "drug/enforcement",
        "drug_enforcement",
        api_key="",
        search='country:"United States"',
        page=1000,
        incremental_cursor="report_date",
        primary_key="recall_number",
        incremental_lag_days=90,
    )
    assert resource["write_disposition"] == "merge"
    assert resource["primary_key"] == "recall_number"
    assert resource["endpoint"]["params"]["sort"] == "report_date:asc"
    assert resource["endpoint"]["params"]["search"] == (
        'country:"United States" AND report_date:[{incremental.start_value} TO *]'
    )
    incremental = resource["endpoint"]["incremental"]
    assert incremental["cursor_path"] == "report_date"
    assert incremental["lag"] == 90
