"""Write counterpart to `ohdp_agent.domains`: publishes a saved dashboard into
OpenMetadata as a Dashboard entity, with lineage from the curated tables its
query touches.

Plain REST via httpx, not the `openmetadata-ingestion` SDK: `apps/api` has
never taken that dependency, and the three calls this needs (upsert a
service, upsert a dashboard, add a lineage edge) are simple enough that the
SDK's connector machinery would be weight with nothing to do. The shapes
below were checked against the SDK's own generated schema
(`metadata.generated.schema.api.services.createDashboardService`,
`...api.data.createDashboard`, `...api.lineage.addLineage`) so they are not a
guess at OpenMetadata's REST contract, just typed by hand instead of through
a pydantic model.

Every dashboard this codebase saves lives under one shared `DashboardService`
(`_SERVICE_NAME`), `serviceType: CustomDashboard` — OpenMetadata's catch-all
for a dashboard source that isn't one of its vendor connectors. `PUT` on both
the service and the dashboard endpoint is OpenMetadata's idempotent upsert
(matching `metadata.create_or_update` in the SDK), so publishing the same
dashboard twice updates it in place rather than erroring or duplicating.

`find_table` matches a Cube cube name to a curated table by exact name, the
same assumption `ohdp_agent.domains.topics_for_tables` already makes: a cube
is named for the dbt model it wraps, and that model is the table. A cube name
that matches nothing contributes no lineage edge — never a guessed one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ohdp_shared import get_logger

log = get_logger(__name__)

# One shared service every saved dashboard is filed under. Its name is also
# its fully qualified name: a top-level service has no parent to qualify it.
_SERVICE_NAME = "ohdp-hub"
_SERVICE_DISPLAY_NAME = "OHDP Hub"


class CatalogWriteError(RuntimeError):
    """OpenMetadata's REST API was unreachable or refused a write."""


class DashboardCatalogClient:
    """Writes dashboards and lineage to OpenMetadata. One instance per call —
    `_service_ensured` only needs to survive a single `publish_dashboard`."""

    def __init__(
        self,
        base_url: str,
        jwt: str,
        *,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._jwt = jwt
        self._timeout = timeout
        self._client = client
        self._service_ensured = False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> dict[str, Any]:
        """One call against OM's REST API. Raises `CatalogWriteError` on any
        non-2xx, so every caller here can use the one catch `dashboard_catalog`
        callers already need for a best-effort write — except a 404 when
        `missing_ok`, which answers `{}` for a delete of something already
        gone."""
        headers = {"Authorization": f"Bearer {self._jwt}"}
        url = f"{self._base_url}{path}"
        if self._client is not None:
            response = await self._client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, url, params=params, json=json_body, headers=headers
                )

        if response.status_code == 404 and missing_ok:
            return {}
        if response.status_code >= 400:
            log.warning("om_write_failed", path=path, status=response.status_code)
            raise CatalogWriteError(f"OpenMetadata returned {response.status_code}")

        return response.json() if response.content else {}

    async def ensure_service(self) -> str:
        """Create the shared `DashboardService` if it doesn't exist yet.
        Idempotent either way, but there is no reason to send the same upsert
        on every save when this instance already knows it succeeded once."""
        if self._service_ensured:
            return _SERVICE_NAME
        await self._request(
            "PUT",
            "/api/v1/services/dashboardServices",
            json_body={
                "name": _SERVICE_NAME,
                "displayName": _SERVICE_DISPLAY_NAME,
                "serviceType": "CustomDashboard",
            },
        )
        self._service_ensured = True
        return _SERVICE_NAME

    async def find_table(self, name: str) -> str | None:
        """The id of the curated table named `name`, or `None` if nothing
        matches. Exact match on `name.keyword`, the same search endpoint
        `ohdp_agent.domains.DomainsClient.assets_in_domain` uses, minus its
        domain filter."""
        query_filter = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"entityType": "table"}},
                        {"term": {"name.keyword": name}},
                    ]
                }
            }
        }
        body = await self._request(
            "GET",
            "/api/v1/search/query",
            params={
                "q": "*",
                "index": "dataAsset",
                "from": "0",
                "size": "1",
                "query_filter": json.dumps(query_filter),
            },
        )
        hits = body.get("hits", {}).get("hits", [])
        if not hits:
            return None
        entity_id = hits[0].get("_source", {}).get("id")
        return str(entity_id) if entity_id else None

    async def publish_dashboard(
        self,
        *,
        name: str,
        title: str,
        description: str,
        url: str,
        table_names: list[str],
        domains: list[str],
    ) -> None:
        """Upsert the Dashboard entity and its lineage from `table_names`.

        `url` becomes the entity's `sourceUrl` — the whole point of this
        being called with the Hub's own `/dashboards/{name}` link rather than
        anything else. `domains` are the same Consumer-aligned topics
        `save_dashboard` already resolved for the library's own listing, so
        the OpenMetadata entity is filed under them too, for free.
        """
        service = await self.ensure_service()
        dashboard = await self._request(
            "PUT",
            "/api/v1/dashboards",
            json_body={
                "name": name,
                "displayName": title,
                "description": description,
                "sourceUrl": url,
                "service": service,
                "domains": domains,
            },
        )
        dashboard_id = dashboard.get("id")
        if not dashboard_id:
            raise CatalogWriteError("OpenMetadata did not return an id for the dashboard.")

        for table_name in table_names:
            table_id = await self.find_table(table_name)
            if table_id is None:
                continue
            await self._request(
                "POST",
                "/api/v1/lineage",
                json_body={
                    "edge": {
                        "fromEntity": {"id": table_id, "type": "table"},
                        "toEntity": {"id": str(dashboard_id), "type": "dashboard"},
                        "lineageDetails": {"source": "DashboardLineage"},
                    }
                },
            )

    async def delete_dashboard(self, name: str) -> None:
        """Hard-delete the Dashboard entity `publish_dashboard` created for
        `name`, lineage edges included.

        Hard, not OpenMetadata's default soft delete: the library row it
        mirrors is gone for good, and a soft-deleted entity would still hold
        its FQN and resurface — stale title, old lineage — if the same name is
        published again. A 404 is success: the dashboard was saved while the
        catalog was down or unconfigured, so there was never an entity to
        remove.

        Addressed by FQN rather than id because the library never stored the
        id. A dashboard name matches `NAME_RE` (kebab-case, no dots), so
        `service.name` needs none of OpenMetadata's FQN quoting.
        """
        await self._request(
            "DELETE",
            f"/api/v1/dashboards/name/{_SERVICE_NAME}.{name}",
            params={"hardDelete": "true", "recursive": "true"},
            missing_ok=True,
        )
