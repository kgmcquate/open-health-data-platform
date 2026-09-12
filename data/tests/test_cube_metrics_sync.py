"""Offline checks for `cube_metrics_sync`'s pure transform logic (Cube meta
response -> OpenMetadata Metric request objects). Nothing here hits the
network."""

from __future__ import annotations


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
    request = _metric_request(cube["measures"][0], dimensions)

    assert str(request.name.root) == "air_quality__avg_value"
    assert request.displayName == "Air Quality Avg Value"
    assert request.metricType.value == "AVERAGE"
    assert str(request.description.root) == "Mean concentration for the parameter over the grain."
    dimension_names = {d.name for d in request.dimensions}
    assert dimension_names == {"measurement_date", "parameter"}


def test_metric_request_maps_sum_agg_type() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    request = _metric_request(_air_quality_cube()["measures"][1], [])

    assert str(request.name.root) == "air_quality__measurement_count"
    assert request.metricType.value == "SUM"
    assert request.description is None


def test_metric_request_falls_back_to_other_for_unknown_agg_type() -> None:
    from ohdp_orchestration.assets.cube_metrics_sync import _metric_request

    request = _metric_request(
        {"name": "air_quality.some_custom_measure", "aggType": "runningTotal"}, []
    )

    assert request.metricType.value == "OTHER"
