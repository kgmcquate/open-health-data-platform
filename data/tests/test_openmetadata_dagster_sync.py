"""Offline checks for `openmetadata_dagster_sync`'s pure transform logic
(GraphQL response -> OpenMetadata request objects, downstream-lineage graph
walk). Nothing here hits the network."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from metadata.generated.schema.type.tagLabel import TagLabel


def _catalog_node(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "assetKey": {"path": ["sources", "healthdata_gov", "perpetrators_trend"]},
        "description": "Child fatalities by state.",
        "tags": [
            {"key": "domain", "value": "healthdata_gov"},
            {"key": "enabled", "value": "true" if enabled else "false"},
        ],
        "dependedByKeys": [{"path": ["ingestion", "healthdata_gov", "perpetrators_trend"]}],
        "metadataEntries": [
            {"label": "cadence", "text": "weekly"},
            {"label": "dagster/uri", "url": "https://healthdata.gov/d/ttus-3dym"},
            {
                "label": "dagster/column_schema",
                "schema": {
                    "columns": [
                        {"name": "state", "type": "text", "description": "The state."},
                        {"name": "_2017", "type": "number", "description": None},
                    ]
                },
            },
        ],
    }


def _fake_tags() -> dict[str, TagLabel]:
    from metadata.generated.schema.type.tagLabel import (
        LabelType,
        State,
        TagFQN,
        TagLabel,
        TagSource,
    )

    def label(fqn: str) -> TagLabel:
        return TagLabel(
            tagFQN=TagFQN(fqn),
            source=TagSource.Classification,
            labelType=LabelType.Automated,
            state=State.Suggested,
        )

    return {
        "Ingestion.Ingested": label("Ingestion.Ingested"),
        "Ingestion.Available": label("Ingestion.Available"),
        "Cadence.Weekly": label("Cadence.Weekly"),
    }


def test_dagster_job_and_schedule_are_registered() -> None:
    from ohdp_orchestration.definitions import defs

    rd = defs.get_repository_def()
    assert "openmetadata_dagster_sync_job" in {j.name for j in rd.get_all_jobs()}
    schedule = next(s for s in rd.schedule_defs if s.name == "openmetadata_dagster_sync_schedule")
    assert schedule.job_name == "openmetadata_dagster_sync_job"
    # STOPPED by default until the JWT is confirmed live in the target environment.
    assert schedule.default_status.value == "STOPPED"


def test_dagster_sync_asset_is_registered() -> None:
    from ohdp_orchestration.definitions import defs

    graph = defs.resolve_asset_graph()
    keys = {k.to_user_string() for k in graph.get_all_asset_keys()}
    assert "openmetadata/openmetadata_dagster_sync" in keys


def test_endpoint_request_from_catalog_node() -> None:
    from ohdp_orchestration.assets.openmetadata_dagster_sync import _endpoint_request

    request = _endpoint_request(_catalog_node(), "healthdata_gov.datasets", _fake_tags())

    assert str(request.name.root) == "perpetrators_trend"
    assert str(request.apiCollection.root) == "healthdata_gov.datasets"
    assert str(request.endpointURL) == "https://healthdata.gov/d/ttus-3dym"
    assert request.tags is not None
    tag_fqns = {str(t.tagFQN.root) for t in request.tags}
    assert tag_fqns == {"Ingestion.Ingested", "Cadence.Weekly"}
    assert request.responseSchema is not None
    assert request.responseSchema.schemaFields is not None
    field_names = {str(f.name.root) for f in request.responseSchema.schemaFields}
    assert field_names == {"state", "_2017"}


def test_endpoint_request_sanitizes_column_names_om_would_reject() -> None:
    """OM rejects the whole APIEndpoint PUT with a 400 when any field name
    breaks its `entityName` pattern, and upstream column names are free text
    — CMS's `innovation_center_milestones_and_updates` really does ship a
    column named `Link ("Learn More")`. The forbidden characters are
    stripped, and an over-long name is still truncated to 128 chars *after*
    stripping."""
    from ohdp_orchestration.assets.openmetadata_dagster_sync import _endpoint_request

    node = _catalog_node()
    node["metadataEntries"][2]["schema"]["columns"] = [
        {"name": 'Link ("Learn More")', "type": "url", "description": None},
        {"name": "a::b", "type": "text", "description": None},
        {"name": 'x"' + "y" * 200, "type": "text", "description": None},
    ]

    request = _endpoint_request(node, "cms.datasets", _fake_tags())

    assert request.responseSchema is not None
    assert request.responseSchema.schemaFields is not None
    field_names = [str(f.name.root) for f in request.responseSchema.schemaFields]
    assert field_names[0] == "Link (Learn More)"
    assert field_names[1] == "a:b"
    assert field_names[2] == "x" + "y" * 127


def test_endpoint_request_tags_dataset_as_available_when_disabled() -> None:
    from ohdp_orchestration.assets.openmetadata_dagster_sync import _endpoint_request

    request = _endpoint_request(
        _catalog_node(enabled=False), "healthdata_gov.datasets", _fake_tags()
    )

    assert request.tags is not None
    tag_fqns = {str(t.tagFQN.root) for t in request.tags}
    assert "Ingestion.Available" in tag_fqns
    assert "Ingestion.Ingested" not in tag_fqns


def test_endpoint_request_works_for_a_source_with_no_domain_specific_metadata() -> None:
    """A hypothetical non-HealthData.gov `sources/` domain that sets none of
    the optional fields (no `dagster/uri`, no column schema, no
    `cadence`/`enabled`) must still get a valid APIEndpoint, not be skipped."""
    from ohdp_orchestration.assets.openmetadata_dagster_sync import _endpoint_request

    node = {
        "assetKey": {"path": ["sources", "some_other_api", "widgets"]},
        "description": "Widgets available from some other API.",
        "tags": [],
        "dependedByKeys": [],
        "metadataEntries": [],
    }

    request = _endpoint_request(node, "some_other_api.datasets", _fake_tags())

    assert str(request.name.root) == "widgets"
    assert str(request.apiCollection.root) == "some_other_api.datasets"
    assert request.endpointURL is None
    assert request.responseSchema is None
    assert request.tags is not None
    tag_fqns = {str(t.tagFQN.root) for t in request.tags}
    assert tag_fqns == {"Ingestion.Available", "Cadence.Weekly"}


def test_find_downstream_snowflake_key_walks_through_ingestion_hop() -> None:
    from ohdp_orchestration.assets.openmetadata_dagster_sync import (
        _find_downstream_snowflake_key,
    )

    source_node = _catalog_node()
    ingestion_node = {
        "assetKey": {"path": ["ingestion", "healthdata_gov", "perpetrators_trend"]},
        "dependedByKeys": [{"path": ["snowflake", "RAW", "healthdata_gov", "perpetrators_trend"]}],
    }
    snowflake_node = {
        "assetKey": {"path": ["snowflake", "RAW", "healthdata_gov", "perpetrators_trend"]},
        "dependedByKeys": [],
    }
    nodes_by_key: dict[tuple[str, ...], dict[str, Any]] = {
        ("ingestion", "healthdata_gov", "perpetrators_trend"): ingestion_node,
        ("snowflake", "RAW", "healthdata_gov", "perpetrators_trend"): snowflake_node,
    }

    result = _find_downstream_snowflake_key(source_node, nodes_by_key)

    assert result == ("snowflake", "RAW", "healthdata_gov", "perpetrators_trend")


def test_find_downstream_snowflake_key_returns_none_when_never_ingested() -> None:
    from ohdp_orchestration.assets.openmetadata_dagster_sync import (
        _find_downstream_snowflake_key,
    )

    source_node = {
        "assetKey": {"path": ["sources", "healthdata_gov", "never_enabled"]},
        "dependedByKeys": [],
    }

    assert _find_downstream_snowflake_key(source_node, {}) is None
