# mypy: disable-error-code="no-untyped-def, no-untyped-call, type-arg, arg-type, override"
"""The dbt medallion project as Dagster assets, under the ``warehouse/`` prefix
(ADR-0012 — dbt-snowflake in prod, dbt-duckdb in local dev/CI).

* models  -> ``warehouse/<model_name>``, grouped ``warehouse_<layer>``
  (`clean` / `core` / `marts`), kinds ``dbt`` + ``snowflake``.
* dbt **sources** map back to the keys the ingestion components already own —
  ``healthdata_gov/<raw_table>`` — so the graph is continuous:

      healthdata_gov/catalog/… → healthdata_gov/<raw_table> (dlt, raw table)
        → warehouse/stg_… (clean) → warehouse/core_… → warehouse/mart_…

Needs ``dbt/target/manifest.json``. ``dagster dev`` builds it (``prepare_if_dev``);
CI and the image run ``dbt parse``. Locally: ``cd data/dbt && uv run dbt parse``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dagster import AssetKey, Definitions
from dagster.components import Component, ComponentLoadContext, Model, Resolvable
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_LAYERS = ("clean", "core", "marts")


class _Translator(DagsterDbtTranslator):
    def __init__(self, key_prefix: str, source_keys: dict[tuple[str, str], list[str]]):
        super().__init__()
        self._prefix = key_prefix
        self._source_keys = source_keys

    def get_asset_key(self, props: dict[str, Any]) -> AssetKey:
        if props["resource_type"] == "source":
            mapped = self._source_keys.get((props["source_name"], props["name"]))
            if mapped:
                return AssetKey(mapped)
        return AssetKey([self._prefix, props["name"]])

    def get_group_name(self, props: dict[str, Any]) -> str | None:
        if props["resource_type"] != "model":
            return None
        fqn = props.get("fqn") or []
        layer = next((p for p in fqn if p in _LAYERS), None)
        return f"{self._prefix}_{layer}" if layer else self._prefix

    def get_tags(self, props: dict[str, Any]) -> dict[str, str]:
        # Drop the bare dbt selection tags (`clean` / `core` / `marts`); keep the
        # rest, add ohdp/* .
        tags = {k: v for k, v in super().get_tags(props).items() if k not in _LAYERS}
        tags["ohdp/domain"] = "warehouse"
        fqn = props.get("fqn") or []
        layer = next((p for p in fqn if p in _LAYERS), None)
        if layer:
            tags["ohdp/layer"] = layer
        return tags

    def get_asset_spec(self, manifest, unique_id, project):
        spec = super().get_asset_spec(manifest, unique_id, project)
        if manifest["nodes"].get(unique_id, {}).get("resource_type") == "model":
            return spec.merge_attributes(kinds={"snowflake"})
        return spec


# Map dbt source (source_name, table_name) -> Dagster AssetKey path, so dbt
# lineage joins the ingestion assets. Extend as sources are added.
_SOURCE_KEYS: dict[tuple[str, str], list[str]] = {}


def _healthdata_gov_source_keys(project_dir: Path) -> dict[tuple[str, str], list[str]]:
    """Every `healthdata_gov` raw table -> ["healthdata_gov", <raw_table>]."""
    import yaml

    out: dict[tuple[str, str], list[str]] = {}
    for path in (project_dir / "models").rglob("*__sources.yml"):
        doc = yaml.safe_load(path.read_text()) or {}
        for source in doc.get("sources", []):
            if source.get("name") != "healthdata_gov":
                continue
            for table in source.get("tables", []):
                out[("healthdata_gov", table["name"])] = ["healthdata_gov", table["name"]]
    return out


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
            self.key_prefix,
            {**_SOURCE_KEYS, **_healthdata_gov_source_keys(project_dir)},
        )
        # `environment`, not credential-presence: a misconfigured prod pod
        # should fail loudly rather than silently build a throwaway local
        # DuckDB file inside the pod.
        target = "prod" if settings.environment == "prod" else "local"
        command = [*self.dbt_command, "--target", target]

        @dbt_assets(
            manifest=project.manifest_path,
            project=project,
            dagster_dbt_translator=translator,
            name="warehouse_dbt",
        )
        def _assets(context, dbt: DbtCliResource):
            yield from dbt.cli(command, context=context).stream()

        return Definitions(assets=[_assets], resources={"dbt": DbtCliResource(project_dir=project)})
