"""Evaluates Dagster AutomationConditions for the Snowflake dbt assets
(``ohdp_orchestration.assets.snowflake_dbt``) — each model opts in (or not)
via its own ``+meta.dagster``/``automaterialize`` config; see
``_Translator.get_automation_condition`` there."""

from __future__ import annotations

from dagster import (
    AssetSelection,
    AutomationConditionSensorDefinition,
    DefaultSensorStatus,
)

from ohdp_orchestration.assets.snowflake_dbt import KEY_PREFIX

snowflake_dbt_automation_sensor = AutomationConditionSensorDefinition(
    name=f"{KEY_PREFIX}_automation",
    target=AssetSelection.groups(
        f"{KEY_PREFIX}_clean",
        f"{KEY_PREFIX}_core",
        f"{KEY_PREFIX}_marts",
    ),
    minimum_interval_seconds=60,
    default_status=DefaultSensorStatus.RUNNING,
    description="Evaluate Dagster AutomationConditions for the Snowflake dbt assets.",
)
