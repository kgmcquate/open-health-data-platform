"""Offline checks for the OpenMetadata sync assets/jobs/schedules (Dagster +
Snowflake sources). Nothing here hits the network — only that they wire up
and the workflow configs they'd run are shaped the way OM expects."""

from __future__ import annotations


def test_dagster_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "openmetadata_dagster_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(s for s in rd.schedule_defs if s.name == "openmetadata_dagster_sync_schedule")
    assert schedule.job_name == "openmetadata_dagster_sync_job"
    # STOPPED by default until the JWT is confirmed live in the target environment.
    assert schedule.default_status.value == "STOPPED"


def test_snowflake_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "openmetadata_snowflake_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(s for s in rd.schedule_defs if s.name == "openmetadata_snowflake_sync_schedule")
    assert schedule.job_name == "openmetadata_snowflake_sync_job"
    assert schedule.default_status.value == "STOPPED"


def test_sync_assets_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.to_user_string() for k in graph.get_all_asset_keys()}
    assert "openmetadata_dagster_sync" in keys
    assert "openmetadata_snowflake_sync" in keys


def test_dagster_workflow_config_shape() -> None:
    from ohdp_orchestration.assets.openmetadata_sync import _dagster_workflow_config

    config = _dagster_workflow_config()
    assert config["source"]["type"] == "dagster"
    assert config["source"]["serviceConnection"]["config"]["type"] == "Dagster"
    assert config["source"]["sourceConfig"]["config"]["lineageInformation"]["dbServiceNames"] == [
        "snowflake"
    ]
    assert config["sink"]["type"] == "metadata-rest"
    assert "hostPort" in config["workflowConfig"]["openMetadataServerConfig"]


def test_snowflake_workflow_config_shape() -> None:
    from ohdp_orchestration.assets.openmetadata_sync import _snowflake_workflow_config

    config = _snowflake_workflow_config()
    assert config["source"]["type"] == "snowflake"
    assert config["source"]["serviceName"] == "snowflake"
    assert config["source"]["serviceConnection"]["config"]["type"] == "Snowflake"
    assert config["source"]["sourceConfig"]["config"]["type"] == "DatabaseMetadata"
    assert config["sink"]["type"] == "metadata-rest"
    assert "hostPort" in config["workflowConfig"]["openMetadataServerConfig"]
