"""The dbt project surfaces as Dagster assets under `warehouse/`, wired into the
ingestion lineage."""

from __future__ import annotations


def test_models_are_warehouse_prefixed_and_grouped_by_layer() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    models = {
        k.path[-1]: graph.get(k) for k in graph.get_all_asset_keys() if k.path[0] == "warehouse"
    }
    assert models, "no warehouse/* assets — did `dbt parse` run?"
    for node in models.values():
        assert "dbt" in node.kinds and "snowflake" in node.kinds
        assert node.group_name in ("warehouse_clean", "warehouse_core", "warehouse_marts")
        assert node.tags["ohdp/domain"] == "warehouse"

    assert "stg_healthdata_gov__hospital_capacity_by_state" in models
    assert models["core_hospital_utilization_daily"].group_name == "warehouse_core"


def test_dbt_sources_resolve_to_the_ingestion_asset_keys() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    stg = graph.get(by_str["warehouse/stg_healthdata_gov__hospital_capacity_by_state"])
    parents = {p.to_user_string() for p in stg.parent_keys}
    # the clean model reads a dlt raw table, not a fresh `warehouse/...` source node
    assert parents == {
        "healthdata_gov/covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    }
    assert not any(p.startswith("warehouse/healthdata_gov") for p in parents)


def test_dbt_resource_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    assert "dbt" in defs.get_repository_def().get_top_level_resources()
