# 0006 — Upstream Helm charts, and the external GraphQL authz proxy

**Status:** Accepted
**Supersedes:** the bespoke `platform/graphql-proxy/` FastAPI stub (deleted)

## Context

Two related questions came up while building the deployment layer:

1. Should we write our own Helm charts for Dagster and OpenMetadata?
2. `kgmcquate/graphql-authz-proxy` already exists and does what §5 requires.
   Should the platform use it instead of a purpose-built proxy?

## Decision

### Use upstream charts; own only the values

`dagster/dagster`, `open-metadata/openmetadata` and `opensearch/opensearch` are
installed from their maintainers' charts. We keep values files in
`platform/helm/values/`. We write charts only for things with no upstream:
`hub-api` and `graphql-authz-proxy`.

Upstream charts encode wiring that is not obvious and changes between releases —
Dagster's webserver/daemon/run-launcher/user-deployment split, OpenMetadata's
migration job and JWT bootstrap. Vendoring it means re-deriving it on every
upgrade for no benefit. Every decision we actually make lives in values anyway.

We do **not** install `openmetadata-dependencies`: it bundles MySQL, Airflow and
OpenSearch. We have Postgres already (§9 — one instance, four databases) and
Dagster does orchestration, so it would add ~2 GB of duplicate infrastructure to
a node with no headroom. OpenSearch is installed standalone.

### Adopt `kgmcquate/graphql-authz-proxy`

It is a better fit than the stub it replaces: config-driven allow/deny policies
with default-deny, argument-level constraints, `X-Forwarded-*` identity from
oauth2-proxy, and a `default_groups` fallback that makes a genuinely public,
read-only Dagster UI possible without enumerating visitors. It also has
Dagster-specific tests upstream.

Configuration lives in `platform/helm/charts/graphql-authz-proxy/values.yaml`:
anonymous users get `public-viewer`, which allows a named list of read-only root
fields and denies all mutations.

## Consequences

- One fewer bespoke component to maintain; policy is now data, not code.
- **The image is pinned by digest.** Upstream publishes only a floating `:latest`
  tag. A security control on a mutable tag is not acceptable, and the chart fails
  the render if `image.digest` is unset.
- **`HOST=0.0.0.0` is set explicitly.** The CLI defaults to `127.0.0.1`; the
  upstream repo's own Kubernetes example omits this and would bind gunicorn to
  loopback, making the pod unreachable through its Service.
- The allowlist is by GraphQL root field name, so a Dagster upgrade that issues
  new queries can break the UI. `make verify-proxy` asserts a mutation is denied
  and a query is allowed; run it after every Dagster upgrade.

### Known upstream issues to resolve before M1 ships publicly

Both found by reading `graphql_authz_proxy/routes.py`; neither is mitigable from
the chart, and the first is a policy bypass.

1. **Only the first operation in a document is authorized.**
   `_check_authorization` returns inside its loop over `document.definitions`
   after the first `OperationDefinitionNode`. A document containing an allowed
   query followed by a mutation passes the check, and `operationName` — which
   selects what the server actually executes — is parsed but never used. This
   defeats the allowlist.

2. **IdP group mapping raises.** `Groups.get_idp_groups()` returns group *names*
   (`list[str]`), and `proxy_graphql` extends `user_groups` — a list of `Group`
   objects — with them. `_collect_field_rules` then reads `.permissions` off a
   `str`, raising into the generic handler and returning 500. Avoided for now by
   using `default_groups` and not configuring `idp_groups`.
