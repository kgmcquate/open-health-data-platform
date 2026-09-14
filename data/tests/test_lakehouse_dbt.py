"""The dbt project surfaces as Dagster assets under `lakehouse/`, wired into the
ingestion lineage (ADR-0019)."""

from __future__ import annotations

_LAYER_GROUPS = ("lakehouse_clean", "lakehouse_core", "lakehouse_marts")


def test_models_are_lakehouse_prefixed_and_grouped_by_layer() -> None:
    """`lakehouse/*` now also contains dbt's raw *sources* — the model check
    below filters to actual dbt models (grouped into one of the three
    medallion layers) so raw sources, which correctly don't carry the `dbt`
    kind, don't fail the assertion."""
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    # Keyed by the full `lakehouse/<catalog>/<namespace>/<name>` string: with
    # `_Translator.get_asset_key` on `[prefix, catalog, namespace, name]`, the
    # trailing name alone is no longer unique across layers.
    lakehouse_nodes = {
        k.to_user_string(): graph.get(k)
        for k in graph.get_all_asset_keys()
        if k.path[0] == "lakehouse"
    }
    assert lakehouse_nodes, "no lakehouse/* assets — did `dbt parse` run?"
    models = {
        name: node for name, node in lakehouse_nodes.items() if node.group_name in _LAYER_GROUPS
    }
    assert models, "no dbt models found under lakehouse/*"
    for node in models.values():
        assert "dbt" in node.kinds and "iceberg" in node.kinds
        assert node.tags["domain"] == "lakehouse"

    assert "lakehouse/lakehouse/clean_healthdata_gov/hospital_capacity_by_state" in models
    assert (
        models["lakehouse/lakehouse/core/hospital_utilization_daily"].group_name == "lakehouse_core"
    )
    # `curated/<mart>/` groups as a mart, `curated/core/` does not — the split
    # `_layer()` exists to make (assets/lakehouse_dbt.py).
    assert (
        models["lakehouse/lakehouse/mart_respiratory/hospital_load"].group_name == "lakehouse_marts"
    )


def test_dbt_sources_resolve_to_the_ingestion_raw_key() -> None:
    """dbt's `healthdata_gov` source resolves to the same
    `lakehouse/<catalog>/raw_<source>/<table>` key the ingestion component
    owns (`SocrataDataset.lakehouse_raw_key`), which is what keeps the graph
    continuous across the dlt/dbt boundary."""
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    stg = graph.get(by_str["lakehouse/lakehouse/clean_healthdata_gov/hospital_capacity_by_state"])
    parents = {p.to_user_string() for p in stg.parent_keys}
    assert parents == {
        "lakehouse/lakehouse/raw_healthdata_gov/"
        "covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    }


def test_healthdata_gov_bridges_the_dlt_table_to_a_raw_asset() -> None:
    """`SocrataDataset._lakehouse_raw_spec()` (components/socrata.py) puts an
    unexecutable raw-layer asset downstream of the dlt table — and, per the
    test above, dbt's compiled source node resolves to that same key."""
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    by_str = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    bridge_str = (
        "lakehouse/lakehouse/raw_healthdata_gov/"
        "covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    )
    assert bridge_str in by_str, "did SocrataDataset._lakehouse_raw_spec() change key shape?"
    bridge = graph.get(by_str[bridge_str])
    parents = {p.to_user_string() for p in bridge.parent_keys}
    assert parents == {
        "ingestion/healthdata_gov/"
        "covid_19_reported_patient_impact_and_hospital_capacity_by_state_timeseries_raw"
    }
    assert bridge.tags["domain"] == "lakehouse"
    assert bridge.tags["layer"] == "raw"


def test_dbt_resource_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    assert "dbt" in defs.get_repository_def().get_top_level_resources()
