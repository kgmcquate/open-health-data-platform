"""Offline checks for `cube_metrics_sync`'s pure transform logic (Cube meta
response -> OpenMetadata Metric request objects). Nothing here hits the
network."""

from __future__ import annotations

import json
from types import SimpleNamespace


def _air_quality_cube() -> dict:
    return {
        "name": "air_quality",
        "dimensions": [
            {"name": "air_quality.measurement_date", "type": "time"},
            {
                "name": "air_quality.parameter",
                "type": "string",
                "description": "pm25, pm10, o3, no2, so2, co",
            },
        ],
        "measures": [
            {
                "name": "air_quality.avg_value",
                "title": "Air Quality Avg Value",
                "aggType": "avg",
                "description": "Mean concentration for the parameter over the grain.",
            },
            {
                "name": "air_quality.measurement_count",
                "title": "Air Quality Measurement Count",
                "aggType": "sum",
            },
        ],
    }


def test_dagster_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "openmetadata_cube_metrics_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(
        s for s in rd.schedule_defs if s.name == "openmetadata_cube_metrics_sync_schedule"
    )
    assert schedule.job_name == "openmetadata_cube_metrics_sync_job"
    # STOPPED by default until the JWT/secret are confirmed live in the target environment.
    assert schedule.default_status.value == "STOPPED"


def test_cube_metrics_sync_asset_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.to_user_string() for k in graph.get_all_asset_keys()}
    assert "openmetadata/openmetadata_cube_metrics_sync" in keys


def test_metric_dimension_maps_time_type() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_dimension

    dimension = _metric_dimension({"name": "air_quality.measurement_date", "type": "time"})

    assert dimension.name == "measurement_date"
    assert dimension.type.value == "TIME"


def test_metric_dimension_defaults_non_time_to_categorical() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_dimension

    dimension = _metric_dimension(
        {"name": "air_quality.parameter", "type": "string", "description": "pm25, pm10"}
    )

    assert dimension.name == "parameter"
    assert dimension.type.value == "CATEGORICAL"
    assert dimension.description == "pm25, pm10"


def test_metric_request_from_cube_measure() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_dimension, _metric_request

    cube = _air_quality_cube()
    dimensions = [_metric_dimension(d) for d in cube["dimensions"]]
    request = _metric_request(cube["measures"][0], dimensions, None, None)

    assert str(request.name.root) == "air_quality__avg_value"
    assert request.displayName == "Air Quality Avg Value"
    assert request.metricType.value == "AVERAGE"
    assert str(request.description.root) == "Mean concentration for the parameter over the grain."
    dimension_names = {d.name for d in request.dimensions}
    assert dimension_names == {"measurement_date", "parameter"}


def test_metric_request_maps_sum_agg_type() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    request = _metric_request(_air_quality_cube()["measures"][1], [], None, None)

    assert str(request.name.root) == "air_quality__measurement_count"
    assert request.metricType.value == "SUM"
    assert request.description is None


def test_metric_request_falls_back_to_other_for_unknown_agg_type() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    request = _metric_request(
        {"name": "air_quality.some_custom_measure", "aggType": "runningTotal"}, [], None, None
    )

    assert request.metricType.value == "OTHER"


def test_metric_request_carries_through_assets() -> None:
    from metadata.generated.schema.type.entityReferenceList import EntityReferenceList

    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    assets = EntityReferenceList([])
    request = _metric_request(_air_quality_cube()["measures"][0], [], assets, None)

    assert request.assets is assets


def test_metric_request_carries_through_related_metrics() -> None:
    from metadata.generated.schema.type.basic import FullyQualifiedEntityName

    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    related = [FullyQualifiedEntityName("air_quality__measurement_count")]
    request = _metric_request(_air_quality_cube()["measures"][0], [], None, related)

    assert request.relatedMetrics == related


def test_metric_request_leaves_related_metrics_unset_when_empty() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    request = _metric_request(_air_quality_cube()["measures"][0], [], None, [])

    assert request.relatedMetrics is None


def test_metric_entity_name_swaps_dot_for_double_underscore() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_entity_name

    assert _metric_entity_name({"name": "air_quality.avg_value"}) == "air_quality__avg_value"


def test_dbt_model_table_index_upper_cases_and_uses_alias(tmp_path, monkeypatch) -> None:
    import ohdp_orchestration.assets.cube_metrics_sync as sync

    manifest = {
        "nodes": {
            "model.ohdp.respiratory__hospital_load": {
                "resource_type": "model",
                "name": "respiratory__hospital_load",
                "database": "curated",
                "schema": "respiratory",
                "alias": "hospital_load",
            },
            "seed.ohdp.some_seed": {
                "resource_type": "seed",
                "name": "some_seed",
                "database": "curated",
                "schema": "respiratory",
            },
        }
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(sync, "_dbt_project", SimpleNamespace(manifest_path=manifest_path))

    index = sync._dbt_model_table_index()

    assert index == {"respiratory__hospital_load": ("CURATED", "RESPIRATORY", "HOSPITAL_LOAD")}


def test_dbt_model_table_index_falls_back_to_name_without_alias(tmp_path, monkeypatch) -> None:
    import ohdp_orchestration.assets.cube_metrics_sync as sync

    manifest = {
        "nodes": {
            "model.ohdp.widgets": {
                "resource_type": "model",
                "name": "widgets",
                "database": "curated",
                "schema": "core",
            },
        }
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(sync, "_dbt_project", SimpleNamespace(manifest_path=manifest_path))

    index = sync._dbt_model_table_index()

    assert index == {"widgets": ("CURATED", "CORE", "WIDGETS")}


def test_resolve_table_returns_none_when_cube_has_no_matching_dbt_model() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _resolve_table

    assert _resolve_table("air_quality", {}, metadata=None) is None
