# platform

Everything that turns one Hetzner CX52 into the running system (ARCHITECTURE.md §4).

| Path | What it does |
|---|---|
| [`terraform/`](terraform) | Hetzner VM + firewall, Cloudflare DNS + R2 buckets, k3s via cloud-init |
| [`helm/charts/hub-api/`](helm/charts/hub-api) | Our chart — the FastAPI backend |
| [`helm/charts/graphql-authz-proxy/`](helm/charts/graphql-authz-proxy) | Our chart — wraps `kgmcquate/graphql-authz-proxy` |
| [`helm/values/`](helm/values) | Values for the upstream Dagster, OpenMetadata and OpenSearch charts |
| `k3s/base/` | Namespaces (`app`, `data`, `bi`, `meta`, `infra`), ingress, cert-manager |
| `scripts/` | Bootstrap, `pg_dump` backup to R2, restore (tested before M4, §11) |

Terraform stops at the cluster boundary. It does not manage Helm releases — a bad
chart should not be able to wedge infrastructure state.

## Order of operations

```bash
cd terraform && terraform apply          # VM, firewall, DNS, R2, k3s
export KUBECONFIG=$PWD/kubeconfig
kubectl apply -k ../k3s/base             # namespaces, cert-manager, ClusterIssuer
# create Postgres + the secrets listed in helm/README.md
cd ../helm && make repos && make install
```

## Non-negotiables

- Memory **limits** on every pod (§4). An unbounded DuckDB query otherwise takes
  the node down. Both of our charts set them; the values files set them for the
  upstream charts.
- Single node, single replica. No HA, no autoscaling, no second Postgres (§9).
- The Dagster Service is never exposed directly — only through oauth2-proxy and
  the authz proxy (§5). An Ingress pointing at `dagster-webserver` would bypass
  the read-only control entirely.
- Nothing in the Dagster UI you would not put on a public webpage (§9).
- cert-manager must use **DNS-01**, not HTTP-01: the firewall admits only
  Cloudflare on 80/443, so Let's Encrypt cannot reach the origin directly.
