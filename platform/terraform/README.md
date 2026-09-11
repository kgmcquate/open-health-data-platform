# Terraform — DigitalOcean Kubernetes + Snowflake

Provisions the managed cluster, the Spaces bucket and the Snowflake data warehouse (ADR-0012), then leaves the application stack to Helm. The old self-hosted k3s-on-droplet bootstrap has been removed.

DNS lives in Cloudflare (`kevinmcquate.com` is a Cloudflare zone). This stack manages the `*.ohdp.kevinmcquate.com` A records through the `cloudflare` provider, DNS-only (not proxied), pointing each host at the Traefik load balancer IP configured in `loadbalancer_ip`. There is no reserved DigitalOcean IP fallback here; the value must be set explicitly in `terraform.tfvars` or via `TF_VAR_loadbalancer_ip`.

## What it creates

| Resource | Notes |
|---|---|
| `digitalocean_kubernetes_cluster` | Managed cluster in the chosen region |
| `digitalocean_kubernetes_node_pool` | Default worker pool (`size`, `node_count`) |
| `digitalocean_spaces_bucket` | `ohdp-warehouse` for backups + the mirrored Snowflake key |
| `cloudflare_dns_record` | One A record per `dns_hostnames` entry under `dns_base`, once `loadbalancer_ip` is set |
| `snowflake_database` + `snowflake_schema` | The warehouse: one database, one schema per medallion layer ([ADR-0012](../../docs/decisions/0012-native-snowflake-tables.md)) |
| `snowflake_account_role` + grants | `OHDP_PIPELINE` — read/write on the warehouse |
| `snowflake_service_user` + `snowflake_network_policy` | The pipeline identity |
| `tls_private_key` | Generates the pipeline's RSA key pair — SERVICE users don't accept password auth |
| `snowflake_warehouse` | XSMALL — compute for the pipeline's dlt loads and dbt-snowflake builds |

This setup intentionally uses a managed DigitalOcean Kubernetes cluster instead of a single self-hosted k3s node. The cluster endpoint and kubeconfig are surfaced via Terraform outputs.

## Usage

```bash
cp terraform.tfvars.example terraform.tfvars   # gitignored; fill it in
terraform init
terraform plan
terraform apply
```

Then pull the kubeconfig:

```bash
terraform output -raw kubeconfig > kubeconfig
chmod 600 kubeconfig
export KUBECONFIG=$PWD/kubeconfig
kubectl get nodes
```

State is local until the Spaces bucket exists. After the first apply, uncomment the `backend "s3"` block in `backend.tf` and run `terraform init -migrate-state`.

## Things that will bite you

- **`admin_ip_ranges` gates the Kubernetes API.** If your IP is dynamic, expect to update this.
- **`user_data` is in `ignore_changes`.** Editing the cloud-init template will not re-bootstrap an existing node.
- **Spaces and S3-compatible backends use the AWS S3 compatibility layer.** Keep `force_path_style = true` and the access keys in GitHub secrets.
- **DigitalOcean does not have the Cloudflare edge WAF.** The DNS records are DNS-only (grey cloud), so the origin LB IP is public and there is no edge WAF or rate limiting. If you want that, switch the records to proxied (orange cloud) — which also requires moving cert-manager to a DNS-01 solver or a Cloudflare Origin CA cert, since HTTP-01 breaks behind the proxy.
- **The DNS records point to the configured Traefik public IP.** Set `loadbalancer_ip` explicitly in `terraform.tfvars` or `TF_VAR_loadbalancer_ip`; if the ingress IP changes, update the variable and re-apply this stack.
- **`CLOUDFLARE_API_TOKEN` must be exported and have `DNS:Edit` on the zone.** The provider reads it from the environment, not a tfvar. A token scoped to the wrong zone or missing the permission fails at apply, not plan.

## Snowflake

Terraform does not create the Snowflake *account* — bring an existing one, and
set `snowflake_organization_name` / `snowflake_account_name` from:

```sql
SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME();
```

Credentials come from the environment, never `terraform.tfvars`:

```bash
export SNOWFLAKE_USER=... SNOWFLAKE_ROLE=ACCOUNTADMIN
export SNOWFLAKE_PRIVATE_KEY="$(cat ~/.snowflake/tf_key.p8)"   # or SNOWFLAKE_PASSWORD
```

After an apply, hand the pipeline's private key to the deploy:

```bash
terraform output -raw snowflake_private_key | gh secret set SNOWFLAKE_PIPELINE_PRIVATE_KEY
```

The other settings (`snowflake_account`, `snowflake_user`, `snowflake_role`,
`snowflake_warehouse`, `snowflake_database`) are non-secret and live in
`pipelineConfig` in `platform/helm/charts/platform-base/values.yaml` — keep
them in sync with those outputs.

### Things that will bite you here too

- **Snowflake SERVICE users don't accept password auth.** `tls_private_key.pipeline`
  generates an RSA key pair; `snowflake_service_user.pipeline.rsa_public_key`
  gets the public half, the private half is `snowflake_private_key`. To
  rotate: `terraform apply -replace=tls_private_key.pipeline`, then re-run the
  `gh secret set` step above and redeploy. The private key is stored in
  Terraform state, so the state Space is as sensitive as the key.
- **`snowflake_allowed_ips` defaults to `0.0.0.0/0`.** It's an account-wide
  network policy, not required for key-pair auth specifically but kept in
  place. Narrow it only once the DOKS egress is stable — node recycles change
  those IPs and a stale entry locks out the whole account, not just the
  pipeline.
- **`terraform destroy` drops the database** — the warehouse's data files, not
  just metadata.
