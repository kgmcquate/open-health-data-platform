"""Allowlist proxy for the Dagster GraphQL endpoint.

Parses each incoming operation, collects the root-level selection fields, and
forwards to the upstream only if every one is on the allowlist. Mutations and
subscriptions are rejected outright.

This is a security control (ARCHITECTURE.md §5). Prefer failing closed: if the
document does not parse, reject it.
"""

from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, Request, Response
from graphql import OperationType, parse
from graphql.language.ast import OperationDefinitionNode

UPSTREAM = os.environ.get("OHDP_DAGSTER_UPSTREAM", "http://dagster-webserver:80")

# Read-only operations the public UI legitimately needs. Extend deliberately,
# with review — never widen to a wildcard.
ALLOWED_ROOT_FIELDS: frozenset[str] = frozenset(
    {
        "runsOrError",
        "pipelineRunsOrError",
        "runOrError",
        "assetNodes",
        "assetNodeOrError",
        "assetsLatestInfo",
        "assetsOrError",
        "repositoriesOrError",
        "repositoryOrError",
        "instigationStatesOrError",
        "instigationStateOrError",
        "runTagsOrError",
        "runGroupOrError",
        "__schema",
        "__type",
    }
)

app = FastAPI(title="Dagster GraphQL allowlist proxy")
_client = httpx.AsyncClient(base_url=UPSTREAM, timeout=30.0)


def _rejected_fields(query: str) -> set[str]:
    """Return the set of disallowed root fields, or {'<parse-error>'} on failure."""
    try:
        document = parse(query)
    except Exception:
        return {"<parse-error>"}

    bad: set[str] = set()
    for definition in document.definitions:
        if not isinstance(definition, OperationDefinitionNode):
            return {"<non-operation-definition>"}
        if definition.operation is not OperationType.QUERY:
            bad.add(f"<{definition.operation.value}>")
            continue
        for selection in definition.selection_set.selections:
            name = getattr(selection, "name", None)
            field = name.value if name else "<unknown>"
            if field not in ALLOWED_ROOT_FIELDS:
                bad.add(field)
    return bad


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/graphql")
async def graphql(request: Request) -> Response:
    body = await request.json()
    operations = body if isinstance(body, list) else [body]

    for op in operations:
        query = (op or {}).get("query", "")
        bad = _rejected_fields(query)
        if bad:
            return Response(
                content=f'{{"errors":[{{"message":"rejected by allowlist proxy: {sorted(bad)}"}}]}}',
                status_code=403,
                media_type="application/json",
            )

    upstream = await _client.post(
        "/graphql",
        content=await request.body(),
        headers={"content-type": "application/json"},
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json"),
    )
