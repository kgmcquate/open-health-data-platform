# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type, override"
"""The dbt medallion project as Dagster assets, under the ``warehouse/`` prefix
(dbt-snowflake only, ADR-0014).

* models  -> ``warehouse/<database>/<schema>/<model_name>``, grouped
  ``warehouse_<layer>`` (`clean` / `core` / `marts`), kinds ``dbt`` + ``snowflake``.
* dbt **sources** map back to the warehouse RAW-layer keys the ingestion
  components already own (``warehouse/RAW/<schema>/<table>``, see
  ``HealthDataGovDataset.warehouse_raw_key``) — so the graph is continuous:

      sources/healthdata_gov/… → ingestion/healthdata_gov/<raw_table> (dlt)
        → warehouse/RAW/… → warehouse/stg_… (clean) → warehouse/core_… → warehouse/mart_…

Needs ``dbt/target/manifest.json``. ``dagster dev`` builds it (``prepare_if_dev``);
CI and the image run ``dbt parse``. Locally: ``cd data/dbt && uv run dbt parse``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dagster import AssetKey, AutomationCondition, Definitions
from dagster.components import Component, ComponentLoadContext, Model, Resolvable
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from ohdp_shared import get_logger

log = get_logger(__name__)

_LAYERS = ("clean", "core", "marts")


class _Translator(DagsterDbtTranslator):
    def __init__(self, key_prefix: str):
        super().__init__()
        self._prefix = key_prefix

    def get_asset_key(self, dbt_resource_props: dict[str, Any]) -> AssetKey:
        """Set the asset key"""

        # Allow the asset key to be overridden if set explicitly in the model config
        hardcoded_asset_key = (
            dbt_resource_props
            .get("config", {})
            .get("meta", {})
            .get("dagster", {})
            .get("asset_key")
        )

        if hardcoded_asset_key:
            return super().get_asset_key(dbt_resource_props)

        # Match the structure of the Snowflake catalog: one database per
        # medallion layer (ADR-0013), a schema per source/mart within it.
        return AssetKey(
            [
                self._prefix,
                dbt_resource_props["database"],
                dbt_resource_props["schema"],
                dbt_resource_props["name"],
            ]
        )

    def get_group_name(self, props: dict[str, Any]) -> str | None:
        if props["resource_type"] != "model":
            return None
        fqn = props.get("fqn") or []
        layer = next((p for p in fqn if p in _LAYERS), None)
        return f"{self._prefix}_{layer}" if layer else self._prefix

    def get_tags(self, props: dict[str, Any]) -> dict[str, str]:
        # Drop the bare dbt selection tags (`clean` / `core` / `marts`); keep the
        # rest, add ohdp/* and the materialization (for asset selection).
        tags = {k: v for k, v in super().get_tags(props).items() if k not in _LAYERS}
        tags["ohdp/domain"] = "warehouse"
        tags["dbt_materialized"] = props.get("config", {}).get("materialized", "view").strip()
        fqn = props.get("fqn") or []
        layer = next((p for p in fqn if p in _LAYERS), None)
        if layer:
            tags["ohdp/layer"] = layer
        return tags

    def get_metadata(self, props: dict[str, Any]) -> dict[str, Any]:
        # Surface a model's dbt `meta:` config (besides `dagster.*`, read below
        # for automation) as asset metadata, e.g. for ownership/doc links.
        metadata = dict(super().get_metadata(props))
        dbt_meta = props.get("config", {}).get("meta", {})
        metadata.update({k: str(v) for k, v in dbt_meta.items()})
        return metadata

    def get_asset_spec(self, manifest, unique_id, project):
        spec = super().get_asset_spec(manifest, unique_id, project)
        if manifest["nodes"].get(unique_id, {}).get("resource_type") == "model":
            return spec.merge_attributes(kinds={"snowflake"})
        return spec

    def get_automation_condition(self, props: dict[str, Any]) -> AutomationCondition | None:
        """Opt-in declarative automation, driven by each model's `+meta.dagster`
        config — nothing currently schedules `warehouse_dbt`, so this is the
        mechanism that lets a model ask to rebuild itself.
        """
        resource_type = props.get("resource_type")
        materialized = props.get("config", {}).get("materialized", "view").strip()
        automaterialize = props.get("config", {}).get("meta", {}).get("automaterialize", False)
        refresh_limit = props.get("config", {}).get("meta", {}).get("refresh_limit")

        # Always rebuild views on code change; everything else needs an opt-in.
        if not automaterialize:
            if materialized == "view":
                return AutomationCondition.code_version_changed()
            return None

        # Only if newly true, so a failed run doesn't re-trigger on its own.
        dbt_model_changed = (
            AutomationCondition.missing() | AutomationCondition.code_version_changed()
        ).newly_true()

        deps_have_updated: AutomationCondition = AutomationCondition.any_deps_updated()

        if refresh_limit is not None:
            cron_passed: AutomationCondition = AutomationCondition.cron_tick_passed(
                refresh_limit
            ).since_last_handled()  # type: ignore[misc]  # dagster generics: AssetKey vs AssetKey | AssetCheckKey
            deps_have_updated = (
                AutomationCondition.any_deps_match(
                    AutomationCondition.newly_updated()
                ).since_last_handled()
                & cron_passed
            )

        if resource_type == "model" and materialized in (
            "table",
            "incremental",
            "view",
            "materialized_view",
        ):
            return (deps_have_updated & ~AutomationCondition.run_in_progress()) | dbt_model_changed

        if resource_type == "seed":
            return dbt_model_changed
        
        return None



def _default_project_dir() -> Path:
    # DBT_PROJECT_DIR is set in the image (Dockerfile) and CI; otherwise resolve
    # data/dbt relative to this installed package.
    env = os.environ.get("DBT_PROJECT_DIR")
    if env:
        return Path(env)
    # .../ohdp_orchestration/defs/warehouse/component.py -> parents[4] == data/
    return Path(__file__).resolve().parents[4] / "dbt"


class DbtWarehouse(Component, Model, Resolvable):
    """The dbt project (`data/dbt`) as Dagster assets under `warehouse/`."""

    # Empty -> $DBT_PROJECT_DIR, else data/dbt next to the package.
    project_dir: str = ""
    key_prefix: str = "warehouse"
    # `dbt build` runs tests inline; a failed test fails the asset (ARCHITECTURE §3).
    dbt_command: list[str] = ["build"]

    def build_defs(self, context: ComponentLoadContext) -> Definitions:
        project_dir = (
            Path(self.project_dir).resolve() if self.project_dir else _default_project_dir()
        )
        project = DbtProject(project_dir=project_dir)
        project.prepare_if_dev()
        if not project.manifest_path.exists():
            raise FileNotFoundError(
                f"{project.manifest_path} missing — run `cd {project_dir} && uv run dbt parse` "
                "(CI and the image do this in the build)."
            )

        translator = _Translator(
            self.key_prefix
        )

        @dbt_assets(
            manifest=project.manifest_path,
            project=project,
            dagster_dbt_translator=translator,
            name="warehouse_dbt",
        )
        def _assets(context, dbt: DbtCliResource):
            yield from dbt.cli(self.dbt_command, context=context).stream()

        return Definitions(assets=[_assets], resources={"dbt": DbtCliResource(project_dir=project)})
