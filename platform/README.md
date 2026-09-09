# platform

Everything that turns one DigitalOcean Kubernetes cluster into the running system.

| Path | What it does |
|---|---|
| [`terraform/`](terraform) | DigitalOcean Kubernetes cluster + DNS + Spaces bucket |
| [`helm/charts/hub-api/`](helm/charts/hub-api) | Our chart — the FastAPI backend |
| [`helm/charts/graphql-authz-proxy/`](helm/charts/graphql-authz-proxy) | Our chart — wraps `kgmcquate/graphql-authz-proxy` |
| [`helm/values/`](helm/values) | Values for the upstream Dagster, OpenMetadata and OpenSearch charts |
| `k3s/base/` | Namespaces (`app`, `data`, `bi`, `meta`, `infra`) + the `letsencrypt-prod` ClusterIssuer |
| `k3s/postgres/` | The shared Postgres StatefulSet (one instance, four databases) |
| `scripts/` | Bootstrap, `pg_dump` backup to Spaces, restore |

Terraform stops at the cluster boundary. It does not manage Helm releases — a bad chart should not be able to wedge infrastructure state.

## Order of operations

```bash
cd terraform && terraform apply          # cluster, DNS, Spaces
export KUBECONFIG=$PWD/kubeconfig
cd ../helm && make repos && make infra   # namespaces, Traefik, cert-manager, ClusterIssuer
kubectl apply -f ../k3s/postgres/postgres.yaml   # after creating postgres-secret
# create the remaining secrets listed in helm/README.md
make install
```

## Non-negotiables

- Memory **limits** on every pod (§4). An unbounded DuckDB query otherwise takes the cluster down. Both of our charts set them; the values files set them for the upstream charts.
- Single node pool, single replica. No HA, no autoscaling, no second Postgres (§9).
- The Dagster Service is never exposed directly — only through oauth2-proxy and the authz proxy (§5). An Ingress pointing at `dagster-webserver` would bypass the read-only control entirely.
- Nothing in the Dagster UI you would not put on a public webpage (§9).
- cert-manager uses **HTTP-01** with the Traefik ingress, since the origin is directly reachable on the cluster and Cloudflare is not in the path.
