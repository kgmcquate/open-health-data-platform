# 0007 — Google login wall in front of Dagster (oauth2-proxy)

**Status:** Accepted
**Amends:** [ADR-0006](0006-upstream-charts-and-external-authz-proxy.md) — the
Dagster UI is no longer anonymously reachable.

## Context

ADR-0006 adopted `graphql-authz-proxy` to make a *publicly* exposed Dagster UI
read-only, with anonymous visitors falling through to a `public-viewer`
allowlist. That shipped, but two things changed the calculus:

1. The run UI leaks more than intended for a fully anonymous surface — run tags,
   config, code-location paths, asset metadata. The redaction filter helps but
   the honest fix is to know who is looking.
2. OpenMetadata now authenticates with Google (a "Web application" OAuth client).
   The same IdP covers Dagster for near-zero marginal cost.

`graphql-authz-proxy` already resolves users from `X-Forwarded-Email` and only
ever needed something upstream to set that header truthfully.

## Decision

Put `oauth2-proxy` (upstream chart `oauth2-proxy/oauth2-proxy`, pinned in the
Makefile) in front of `graphql-authz-proxy`. It is the only ingress to Dagster.

```
Cloudflare -> Traefik -> Ingress(dagster.ohdp) -> oauth2-proxy -> graphql-authz-proxy -> dagster-webserver
```

- **Provider `google`, `email_domains = ["*"]`.** Any Google account may sign in.
  There is no anonymous access. Authorization is still `graphql-authz-proxy`'s
  job: `kgmcquate@gmail.com` → `admin`, every other authenticated user →
  `public-viewer` (the same read-only allowlist as before).
- **A dedicated OAuth client**, not the OpenMetadata one. Redirect URI
  `https://dagster.ohdp.kevinmcquate.com/oauth2/callback`.
- **Secret split follows the existing rule.** `platform-base` generates the
  session-cookie key (`data/oauth2-proxy-secret`, key `cookie-secret`, read back
  on every upgrade so sessions survive a redeploy). The `External secrets` step
  of `deploy-platform.yml` merges `client-id` / `client-secret` into the same
  Secret from repo secrets `DAGSTER_OIDC_CLIENT_ID` / `DAGSTER_OIDC_CLIENT_SECRET`.
- **`graphql-authz-proxy` keeps no ingress.** Its Service is ClusterIP; oauth2-proxy
  reaches it in-cluster. Its `validateToken` stays `false` — spoofing the
  identity header now requires a foothold in the `data` namespace.

## Consequences

- The `public-viewer` allowlist and `make verify-proxy` are unchanged and still
  load-bearing: a signed-in stranger has exactly the access an anonymous visitor
  used to have. The default-deny regression guard (`.ci/assert_authz_policy.py`)
  still applies.
- **The old `graphql-authz-proxy` Ingress is removed.** It was templated from a
  `templates/ingress.yaml` that was never committed (a local file during a manual
  `helm upgrade`), so it is pruned the next time `make proxy` runs from repo state.
  oauth2-proxy's chart Ingress takes over `dagster.ohdp` and the `dagster-tls`
  cert; expect one cert-manager reissue on the cutover deploy.
- One more pod on a node already near its memory-request ceiling. oauth2-proxy is
  a small Go binary (req 25Mi / lim 64Mi). If it goes Pending, bump the node size
  in `platform/terraform/variables.tf`.
- Losing the Google OAuth client, or a misconfigured redirect URI, now takes the
  Dagster UI down entirely rather than degrading it to anonymous read-only.
