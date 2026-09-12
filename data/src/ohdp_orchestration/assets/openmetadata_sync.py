# The @asset bodies' `context` param is resolved by Dagster, not annotated
# here — `_validate_context_type_hint` compares the raw (string, under
# `annotations` import) annotation object-identically, so a typed annotation
# always fails.
# mypy: disable-error-code="no-untyped-def"
"""Pulls metadata into OpenMetadata via OM's own connectors
(``openmetadata-ingestion[dagster,snowflake]``) rather than a bespoke
integration — ARCHITECTURE.md §2, §6. Each source runs its
``metadata.workflow.metadata.MetadataWorkflow`` in-process — the same API
OpenMetadata's own Airflow/Dagster integration snippets use.

* ``openmetadata_dagster_sync`` — pipeline/job/run metadata, read through
  ``graphql-authz-proxy`` rather than the raw ``dagster-webserver`` Service —
  same reasoning as ``dagster-monitoring``
  (``platform/helm/charts/dagster-monitoring/values.yaml``): a pod-to-pod
  caller with none of oauth2-proxy's ``X-Forwarded-*`` headers falls into the
  proxy's public-viewer group, so this job is bound by the same read-only
  allowlist as everyone else rather than a side channel around it. See
  ``dagster_graphql_url`` in ``ohdp_shared.settings``.
* ``openmetadata_snowflake_sync`` — database/schema/table/column metadata,
  authenticating the same key-pair way dlt and dbt-snowflake already do
  (``ohdp_shared.settings.snowflake_*``). No ``database``/filter patterns: the
  ``OHDP_PIPELINE`` role only has grants on the medallion-layer databases
  (RAW/CLEAN/CURATED, ADR-0013) in the first place, so there is nothing else
  for it to see. Its service name is what the Dagster asset's
  ``lineageInformation`` points at, so pipeline runs link up with the table
  lineage dbt/dlt publish under ``snowflake/``.
* ``openmetadata_dbt_sync`` — model descriptions/tags/tests and source/ref
  lineage from the dbt project, run the "external" way (`run-dbt-workflow-
  externally`): a ``dbt`` source pointed at a manifest, not a CLI wrapper.
  Reads ``target/manifest.json`` baked into this image at build time
  (``Dockerfile``'s ``dbt parse`` step — same file
  ``ohdp_orchestration.assets.snowflake_dbt`` builds ``snowflake_dbt_assets``
  from), not a live ``dbt build``'s output: each Dagster *run* gets its own
  ephemeral pod (``K8sRunLauncher``), so this asset's pod never sees the
  ``run_results.json``/``catalog.json`` a same-day ``snowflake_dbt_assets``
  run produced in its own pod. ``dbt parse``'s manifest already carries
  model/test/lineage/tag metadata, which is what this ingestion is for;
  column types/descriptions still come from ``openmetadata_snowflake_sync``.
  Attaches to the same ``serviceName`` as that asset so dbt models overlay
  onto the tables it already ingested — schedule this one to run after it.

``openmetadata-ingestion``'s core pulls in ``collate-sqllineage==2.1.7``,
which hard-pins ``sqlglot==29.0.1`` — a version no released ``dagster-dbt``
happens to also allow, so plain ``uv add`` here is unsatisfiable. Forced with
``[tool.uv] override-dependencies`` in ``data/pyproject.toml`` (confirmed:
``dagster-dbt``'s own sqlglot usage — dbt manifest parsing — works fine
against 29.0.1, per the `defs/snowflake/` component's own test suite).

Jobs/schedules for these live in ``ohdp_orchestration.jobs``/``.schedules``,
not here — this module is asset bodies only (``ohdp_orchestration.assets``'s
own convention, see its ``__init__.py``).
"""

from __future__ import annotations

from typing import Any

from dagster import asset
from metadata.workflow.metadata import MetadataWorkflow

from ohdp_orchestration.assets.snowflake_dbt import _project as _dbt_project
from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

_DAGSTER_SERVICE_NAME = "ohdp_dagster"
_SNOWFLAKE_SERVICE_NAME = "snowflake"

_GROUP_NAME = "openmetadata_sync"
_KEY_PREFIX = "openmetadata"


def _openmetadata_server_config() -> dict[str, Any]:
    return {
        "hostPort": "https://catalog.open-health-data-platform.org/api",
        "authProvider": "openmetadata",
        "securityConfig": {"jwtToken": settings.openmetadata_jwt},
    }


def _dagster_workflow_config() -> dict[str, Any]:
    return {
        "source": {
            "type": "dagster",
            "serviceName": _DAGSTER_SERVICE_NAME,
            "serviceConnection": {
                "config": {
                    "type": "Dagster",
                    "host": settings.dagster_graphql_url,
                }
            },
            "sourceConfig": {
                "config": {
                    "type": "PipelineMetadata",
                    "includeLineage": True,
                    "lineageInformation": {"dbServiceNames": [_SNOWFLAKE_SERVICE_NAME]},
                }
            },
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": {
            "loggerLevel": "INFO",
            "openMetadataServerConfig": _openmetadata_server_config(),
        },
    }


def _snowflake_workflow_config() -> dict[str, Any]:
    return {
        "source": {
            "type": "snowflake",
            "serviceName": _SNOWFLAKE_SERVICE_NAME,
            "serviceConnection": {
                "config": {
                    "type": "Snowflake",
                    "username": settings.snowflake_user,
                    "privateKey": settings.snowflake_private_key,
                    "account": settings.snowflake_account,
                    "role": settings.snowflake_role,
                    "warehouse": settings.snowflake_warehouse,
                }
            },
            "sourceConfig": {
                "config": {
                    "type": "DatabaseMetadata",
                    "markDeletedTables": True,
                    "markDeletedSchemas": True,
                    "markDeletedDatabases": True,
                    "includeTags": True,
                    "includeTables": True,
                    "includeViews": True,
                    "includeOwners": True,
                    "overrideMetadata": True,
                    "threads": 4,
                    "incremental": {
                        "enabled": True,
                        "lookbackDays": 7,
                        "safetyMarginDays": 1,
                    },
                    "databaseFilterPattern": {
                        "includes": ["RAW", "CLEAN", "CURATED"]
                    },
                    "schemaFilterPattern": {
                        "excludes": [
                            "INFORMATION_SCHEMA",
                            "PUBLIC",
                            "DBT_TEST__AUDIT"
                        ]
                    },
                    "tableFilterPattern": {
                        "excludes": [
                            "_DLT_.*"
                        ]
                    },
                }
            },
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": {
            "loggerLevel": "INFO",
            "openMetadataServerConfig": _openmetadata_server_config(),
        },
    }


def _dbt_workflow_config() -> dict[str, Any]:
    return {
        "source": {
            "type": "dbt",
            "serviceName": _SNOWFLAKE_SERVICE_NAME,
            "sourceConfig": {
                "config": {
                    "type": "DBT",
                    "dbtConfigSource": {
                        "dbtConfigType": "local",
                        "dbtManifestFilePath": str(_dbt_project.manifest_path),
                    },
                    "dbtUpdateDescriptions": True,
                    "includeTags": True,
                    "databaseFilterPattern": {
                        "includes": ["CLEAN", "CURATED"],
                        "excludes": [],
                    },
                }
            },
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": {
            "loggerLevel": "INFO",
            "openMetadataServerConfig": _openmetadata_server_config(),
        },
    }


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def openmetadata_dagster_sync(context) -> None:
    """Runs the ``dagster`` source's ``MetadataWorkflow`` against this
    instance."""
    context.log.info(
        f"Ingesting Dagster metadata into OpenMetadata via {settings.dagster_graphql_url}"
    )
    workflow = MetadataWorkflow.create(_dagster_workflow_config())
    workflow.execute()
    workflow.print_status()
    workflow.raise_from_status()
    workflow.stop()


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def openmetadata_snowflake_sync(context) -> None:
    """Runs the ``snowflake`` source's ``MetadataWorkflow`` against
    ``settings.snowflake_account``."""
    context.log.info(
        f"Ingesting Snowflake metadata into OpenMetadata via {settings.snowflake_account}"
    )
    workflow = MetadataWorkflow.create(_snowflake_workflow_config())
    workflow.execute()
    workflow.print_status()
    workflow.raise_from_status()
    workflow.stop()


@asset(key_prefix=_KEY_PREFIX, group_name=_GROUP_NAME, kinds={"openmetadata"})
def openmetadata_dbt_sync(context) -> None:
    """Runs the ``dbt`` source's ``MetadataWorkflow`` against the manifest
    baked into this image, attaching model descriptions/tags/tests/lineage to
    the tables ``openmetadata_snowflake_sync`` already ingested."""
    context.log.info(
        f"Ingesting dbt metadata into OpenMetadata from {_dbt_project.manifest_path}"
    )
    workflow = MetadataWorkflow.create(_dbt_workflow_config())
    workflow.execute()
    workflow.print_status()
    workflow.raise_from_status()
    workflow.stop()
