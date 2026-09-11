"""Offline checks for the config-driven HealthData.gov ingestion.

Nothing here hits the network: dlt sources are lazy, so building the Definitions
object only parses the component instances and wires assets/jobs/schedules.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ohdp_ingestion.healthdata_gov.config import DatasetConfig

_DEFS = Path(__file__).resolve().parents[1] / "src/ohdp_orchestration/defs/healthdata_gov"
_DATASETS_DIR = _DEFS / "datasets"


def _instance_paths() -> list[Path]:
    return sorted(_DATASETS_DIR.glob("*/defs.yaml"))


def _config(path: Path) -> DatasetConfig:
    doc = yaml.safe_load(path.read_text())
    return DatasetConfig.model_validate(doc["attributes"])


def _all_configs() -> list[DatasetConfig]:
    return [_config(p) for p in _instance_paths()]


def _catalog_key(cfg: DatasetConfig) -> str:
    return f"healthdata_gov/catalog/{cfg.raw_table}"


def _table_key(cfg: DatasetConfig) -> str:
    return f"healthdata_gov/{cfg.raw_table}"


def test_scraper_has_run() -> None:
    assert _instance_paths(), "run data/scripts/scrape_healthdata_gov.py first"


@pytest.mark.parametrize("path", _instance_paths(), ids=lambda p: p.parent.name)
def test_every_instance_is_a_valid_dataset_component(path: Path) -> None:
    doc = yaml.safe_load(path.read_text())
    assert doc["type"].endswith("HealthDataGovDataset")
    cfg = DatasetConfig.model_validate(doc["attributes"])
    assert re.fullmatch(r"[a-z][a-z0-9_]*", cfg.raw_table)
    assert cfg.cadence in ("daily", "weekly", "monthly")


def test_raw_table_names_are_unique() -> None:
    tables = [c.raw_table for c in _all_configs()]
    assert len(tables) == len(set(tables))


def test_every_dataset_has_a_catalog_asset() -> None:
    from ohdp_orchestration.definitions import defs

    keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    for cfg in _all_configs():
        assert _catalog_key(cfg) in keys


def test_only_enabled_datasets_get_a_table_asset_downstream_of_catalog() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    for cfg in _all_configs():
        catalog = graph.get(by_str[_catalog_key(cfg)])
        assert catalog.is_materializable is False

        table_str = _table_key(cfg)
        if cfg.enabled:
            table = graph.get(by_str[table_str])
            assert table.is_materializable is True
            assert by_str[_catalog_key(cfg)] in table.parent_keys
            assert by_str[table_str] in catalog.child_keys
        else:
            assert table_str not in by_str
            assert catalog.child_keys == set()


def test_catalog_assets_carry_column_schema_and_metadata() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}
    for cfg in _all_configs():
        if not cfg.columns:
            continue
        node = graph.get(by_str[_catalog_key(cfg)])
        schema = node.metadata["dagster/column_schema"]
        assert {c.name for c in schema.columns} == {c.name for c in cfg.columns}
        assert node.metadata["socrata_id"] == cfg.id
        assert node.tags["ohdp/enabled"] == str(cfg.enabled).lower()


def test_three_cadence_jobs_and_schedules_regardless_of_enabled_set() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    hd_jobs = {j.name for j in rd.get_all_jobs() if j.name.startswith("healthdata_gov_")}
    assert hd_jobs == {
        "healthdata_gov_daily_ingest",
        "healthdata_gov_weekly_ingest",
        "healthdata_gov_monthly_ingest",
    }
    assert {s.name for s in rd.schedule_defs} == {
        "healthdata_gov_daily_schedule",
        "healthdata_gov_weekly_schedule",
        "healthdata_gov_monthly_schedule",
    }

    # every enabled dataset lands in exactly its cadence job
    enabled = [c for c in _all_configs() if c.enabled]
    per_job = {
        j.name: {k.to_user_string() for k in j.asset_layer.executable_asset_keys}
        for j in rd.get_all_jobs()
        if j.name in hd_jobs
    }
    for cadence in ("daily", "weekly", "monthly"):
        want = {f"healthdata_gov/{c.raw_table}" for c in enabled if c.cadence == cadence}
        assert per_job[f"healthdata_gov_{cadence}_ingest"] == want
