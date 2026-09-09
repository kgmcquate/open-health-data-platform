# Dagster GraphQL allowlist proxy

Dagster is publicly exposed. Its read-only mode is UI-level only — **this proxy is
what enforces read-only** (ARCHITECTURE.md §5).

Design:
- **Allowlist, not denylist.** Only the operations in `ALLOWED_ROOT_FIELDS` pass.
  Everything else is 403. A denylist silently reopens on every Dagster upgrade.
- Rejects: mutations, subscriptions, any query touching a root field not on the
  list, batched operations containing a disallowed field, introspection beyond a
  minimal allowance (optional).
- Sits behind oauth2-proxy (which gates the hostname) and in front of
  `dagster-webserver`.
- Rate limiting is done at Cloudflare, not here.

## Run

```bash
uvicorn proxy.main:app --port 8080
# env: OHDP_DAGSTER_UPSTREAM=http://dagster-webserver:80
```
