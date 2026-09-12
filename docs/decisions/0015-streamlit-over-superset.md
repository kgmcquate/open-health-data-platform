# 0015 — Streamlit over Superset

**Status:** Accepted
**Supersedes:** the Superset-based dashboard deployment described in earlier
drafts of ARCHITECTURE.md and `platform/helm/charts/superset` (removed by
this decision).

## Context

Superset was deployed via the Superset Kubernetes Operator (CRDs, a
controller, a `Superset` CR, its own Postgres metastore database, and a
hand-rolled Valkey cache) plus a dedicated oauth2-proxy wall. Standing that up
and keeping it running proved to be a disproportionate amount of operational
complexity for the value delivered at this stage — no Celery worker/beat
(scheduled reports and async SQL Lab unavailable), one operator, no
self-service users yet.

Separately, most dashboards on this platform are expected to be AI-authored,
not built by hand through Superset's chart-builder UI. A code-first tool is a
better fit for that workflow: an LLM can write a dashboard as a plain Python
file and open a PR, the same way it would for any other code change here.

## Decision

Replace Superset with Streamlit, deployed as a plain Kubernetes `Deployment`
via our own chart (`platform/helm/charts/streamlit`) — no operator, no
metastore. Each dashboard is a file under `apps/streamlit/pages/`; Streamlit's
multipage support picks up new ones automatically.

- **Direct Snowflake, not Cube, for now.** Cube isn't deployed in the cluster
  yet (M2 target) — this matches Superset's actual M1 wiring, not the
  aspirational Cube-fronted one. `apps/streamlit/lib/snowflake_client.py`
  connects with the existing `OHDP_PIPELINE` role/key (the same credential
  dlt/dbt-snowflake use), mirrored into the `bi` namespace by
  `deploy-platform.yml`.
- **Same Google-wall pattern, renamed.** `oauth2-proxy-streamlit` replaces
  `oauth2-proxy-superset` unchanged in shape — `kgmcquate@gmail.com` only,
  namespace `bi`, same host-rename (`streamlit.open-health-data-platform.org`).
  Unlike Superset, Streamlit has no login system of its own, so this wall is
  its only authn/authz.
- **No embedding, no guest tokens.** Superset's guest-token embed model (hub
  app iframe with row-level security) has no Streamlit equivalent, and
  nothing consumed it yet — `hub_api`'s `/dashboards` route was an unbuilt
  comment. Streamlit is linked from the hub app as a standalone destination
  instead. Embedding with per-user row-level security is a real design
  problem to solve later, if the product actually needs it — likely via
  Cube's existing service-token/`queryRewrite` pattern once M2 lands, not a
  Streamlit-specific mechanism.
- **No new Postgres database.** Superset needed a metastore for stored
  dashboard/chart/user objects; Streamlit dashboards are Python files in git,
  so there is nothing to persist. The `superset` database, `superset-db-auth`,
  and `superset-secret` are removed rather than carried forward unused.

## Consequences

- **Lost, deliberately:** Superset's RBAC, its dataset/metric semantic layer,
  ad-hoc SQL Lab exploration, and scheduled alerts/reports. If self-service
  exploration by non-technical users becomes a real requirement, that is a
  reason to reconsider — Streamlit does not replace that class of tool.
- **Privilege trade-off, temporary:** Streamlit reuses the pipeline's
  Snowflake role, which can also write/DDL the warehouse — more privilege
  than a query-only workload needs. A dedicated read-only reporting role
  (new `snowflake_account_role` + SERVICE user in
  `platform/terraform/snowflake.tf`, granted `SELECT` only on mart schemas)
  is a documented follow-up, not part of this change.
- **Resource budget improves.** ~1 GB (Superset web server + Valkey) drops to
  ~0.5 GB (Streamlit alone) — see ARCHITECTURE.md §4.
- **Operational surface shrinks.** No CRDs, no controller, no operator
  namespace, no migration Jobs, one less Postgres database to back up.
