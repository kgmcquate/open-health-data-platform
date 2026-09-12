"""Offline checks for the OpenMetadata Snowflake/dbt sync assets/jobs/
schedules. Nothing here hits the network — only that they wire up and the
workflow configs they'd run are shaped the way OM expects.
``openmetadata_dagster_sync`` has its own module and its own test file
(``test_openmetadata_dagster_sync.py``)."""

from __future__ import annotations


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
    assert "openmetadata_snowflake_sync" in keys


def test_snowflake_workflow_config_shape() -> None:
    from ohdp_orchestration.assets.openmetadata_sync import _snowflake_workflow_config

    config = _snowflake_workflow_config()
    assert config["source"]["type"] == "snowflake"
    assert config["source"]["serviceName"] == "snowflake"
    assert config["source"]["serviceConnection"]["config"]["type"] == "Snowflake"
    assert config["source"]["sourceConfig"]["config"]["type"] == "DatabaseMetadata"
    assert config["sink"]["type"] == "metadata-rest"
    assert "hostPort" in config["workflowConfig"]["openMetadataServerConfig"]
