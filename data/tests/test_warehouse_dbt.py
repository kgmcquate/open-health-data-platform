"""The dbt project surfaces as Dagster assets under `warehouse/`, wired into the
ingestion lineage."""

from __future__ import annotations


_LAYER_GROUPS = ("warehouse_clean", "warehouse_core", "warehouse_marts")


def test_models_are_warehouse_prefixed_and_grouped_by_layer() -> None:
    """`warehouse/*` now also contains dbt's raw *sources* — the model check
    below filters to actual dbt models (grouped into one of the three
    medallion layers) so raw sources, which correctly don't carry the `dbt`
    kind, don't fail the assertion."""
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    warehouse_nodes = {
        k.path[-1]: graph.get(k) for k in graph.get_all_asset_keys() if k.path[0] == "warehouse"
    }
    assert warehouse_nodes, "no warehouse/* assets — did `dbt parse` run?"
    models = {name: node for name, node in warehouse_nodes.items() if node.group_name in _LAYER_GROUPS}
    assert models, "no dbt models found under warehouse/*"
    for node in models.values():
        assert "dbt" in node.kinds and "snowflake" in node.kinds
        assert node.tags["ohdp/domain"] == "warehouse"

    assert "stg_healthdata_gov__hospital_capacity_by_state" in models
    assert models["core_hospital_utilization_daily"].group_name == "warehouse_core"


def test_dbt_sources_are_warehouse_prefixed() -> None:
    """dbt's `healthdata_gov` source no longer resolves to the flat
    `healthdata_gov/<raw_table>` ingestion key (that mapping was removed when
    `_Translator.get_asset_key` switched to `[prefix, database, schema, name]`
    for every resource type). The source's parent is now a `warehouse/...` key
    computed from whatever target `dbt/target/manifest.json` was parsed
    against — not necessarily the same key `_warehouse_raw_spec()` below
    predicts, since that manifest is baked with `--target ci`/`local`, never
    `prod` (see `HealthDataGovDataset.warehouse_raw_key`'s docstring)."""
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    stg_key = next(k for k in by_str if k.endswith("stg_healthdata_gov__hospital_capacity_by_state"))
    stg = graph.get(by_str[stg_key])
    parents = {p.to_user_string() for p in stg.parent_keys}
    assert len(parents) == 1
    (source_parent,) = parents
    assert source_parent.startswith("warehouse/")
    assert source_parent.endswith(
        "healthdata_gov/covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    )


def test_healthdata_gov_bridges_the_dlt_table_to_a_warehouse_raw_asset() -> None:
    """`HealthDataGovDataset._warehouse_raw_spec()` (healthdata_gov/component.py)
    puts an unexecutable `warehouse/RAW/<schema>/<table>` asset downstream of
    the dlt table — a best-effort RAW-layer label, not (yet) dbt's actual
    compiled source key; see `warehouse_raw_key`'s docstring for why."""
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    bridge_str = (
        "warehouse/RAW/healthdata_gov/"
        "covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    )
    assert bridge_str in by_str, "did HealthDataGovDataset._warehouse_raw_spec() change key shape?"
    bridge = graph.get(by_str[bridge_str])
    parents = {p.to_user_string() for p in bridge.parent_keys}
    assert parents == {
        "healthdata_gov/covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    }
    assert bridge.tags["ohdp/domain"] == "warehouse"
    assert bridge.tags["ohdp/layer"] == "raw"


def test_dbt_resource_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    assert "dbt" in defs.get_repository_def().get_top_level_resources()
