# Terraform — DigitalOcean Kubernetes + the AWS lakehouse + Snowflake

Provisions the managed cluster, the Spaces buckets, and the Iceberg lakehouse —
Snowflake as the catalog, an S3 external volume as its storage
([ADR-0019](../../docs/decisions/0019-iceberg-on-s3-duckdb-dbt.md)) — then
leaves the application stack to Helm. The old self-hosted k3s-on-droplet
bootstrap has been removed.

Three providers, three accounts: DigitalOcean (cluster, Spaces), Snowflake (the
catalog, and the query engine over it) and AWS (storage only — no Glue, no
catalog, just a bucket Snowflake writes into). Cloudflare makes four, for DNS.

DNS lives in Cloudflare (`open-health-data-platform.org` is a Cloudflare zone). This stack manages the `*.open-health-data-platform.org` A records through the `cloudflare` provider, DNS-only (not proxied), pointing each host at the Traefik load balancer IP configured in `loadbalancer_ip`. There is no reserved DigitalOcean IP fallback here; the value must be set explicitly in `terraform.tfvars` or via `TF_VAR_loadbalancer_ip`.

## What it creates

| Resource | Notes |
|---|---|
| `digitalocean_kubernetes_cluster` | Managed cluster in the chosen region |
| `digitalocean_kubernetes_node_pool` | Default worker pool (`size`, `node_count`) |
| `digitalocean_spaces_bucket` | `ohdp-warehouse` for backups + the mirrored Snowflake key |
| `cloudflare_dns_record` | One A record per `dns_hostnames` entry under `dns_base`, once `loadbalancer_ip` is set |
| `aws_s3_bucket` | `ohdp-lakehouse` — the external volume's storage. No Glue, no catalog |
| `aws_iam_user` + `aws_iam_access_key` | The pipeline's AWS identity, for dlt's Parquet writes only |
| `aws_iam_role` | Assumed by Snowflake's external volume to manage the files |
| `snowflake_execute` | External volume, the RAW/CLEAN/CURATED catalog databases, their namespaces, and grants |
| `snowflake_user_programmatic_access_token` | The pipeline's credential for the Iceberg REST endpoint |
| `snowflake_account_role` + grants | `OHDP_PIPELINE` — read/write on all three catalogs |
| `snowflake_service_user` + `snowflake_network_policy` | The one identity; the policy is required for PATs |
| `tls_private_key` | Its RSA key pair, for the SQL connector — SERVICE users don't accept password auth |
| `snowflake_warehouse` | XSMALL — compute for Cube's and Streamlit's queries (no dbt builds) |

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

## The lakehouse

Snowflake is the catalog; the storage is ours. The table files live in our own
S3 bucket, reached through an external volume — Snowflake holds the metadata,
not the bytes. The medallion layers are Iceberg tables in the same
RAW/CLEAN/CURATED databases they always used (ADR-0013) —
a Snowflake database is an Iceberg catalog and its schemas are that catalog's
namespaces, so no names moved. Snowflake serves all three over the open Iceberg
REST protocol (Horizon), so DuckDB writes and Snowflake reads the same tables:

```
https://<org>-<account>.snowflakecomputing.com/polaris/api/catalog
```

Two credentials, one user: a **programmatic access token** for that REST
endpoint (dlt and dbt/DuckDB) and the **RSA key pair** for SQL (Cube and
Streamlit). PATs only work on a user covered by a network policy, which is why
the account-wide one in `snowflake.tf` is load-bearing rather than vestigial.

AWS is storage only. Terraform authenticates to it with **admin** credentials
you supply — separate from the pipeline IAM user it creates:

```bash
export TF_VAR_aws_access_key_id=... TF_VAR_aws_secret_access_key=...
```

Deliberately `TF_VAR_`-prefixed, not `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`:
those two belong to the **Spaces** state backend (`backend.tf`), which is also
"S3". Setting both to the same thing breaks one of them.

After an apply, publish the three pipeline credentials to the repo secrets the
deploy reads:

```bash
terraform output -raw snowflake_pipeline_pat         | gh secret set SNOWFLAKE_PIPELINE_PAT
terraform output -raw snowflake_private_key          | gh secret set SNOWFLAKE_PIPELINE_PRIVATE_KEY
terraform output -raw aws_pipeline_access_key_id     | gh secret set AWS_PIPELINE_ACCESS_KEY_ID
terraform output -raw aws_pipeline_secret_access_key | gh secret set AWS_PIPELINE_SECRET_ACCESS_KEY
```

The AWS pair is needed for one job: dlt writes each raw table's Parquet into
the volume's bucket itself and cannot use the short-lived credentials Horizon
vends. DuckDB does use those, and holds no AWS key.

The non-secret half (`aws_region`, `lakehouse_bucket`) lives in
`pipelineConfig` in `platform/helm/charts/platform-base/values.yaml` — keep it
in sync with those outputs. The catalog endpoint is derived from the account
identifier, so nothing carries it.

### The second apply

The external volume generates an IAM user ARN and an external ID **when
Snowflake creates it**, and the AWS role it assumes can only trust those values
afterwards. So the first apply leaves that role deliberately un-assumable and
Snowflake cannot touch the bucket yet. Finish it:

```bash
terraform apply                       # creates everything; storage not yet reachable

# In Snowsight (the name comes from `terraform output snowflake_trust_policy_inputs`):
DESC EXTERNAL VOLUME OHDP_LAKEHOUSE_VOL;   -- STORAGE_AWS_IAM_USER_ARN, STORAGE_AWS_EXTERNAL_ID

export TF_VAR_snowflake_storage_aws_iam_user_arn=arn:aws:iam::...:user/...
export TF_VAR_snowflake_storage_aws_external_id=...

terraform apply                       # rewrites the trust policy
```

Then check it from Snowflake:

```sql
SELECT SYSTEM$VERIFY_EXTERNAL_VOLUME('OHDP_LAKEHOUSE_VOL');
SHOW SCHEMAS IN DATABASE CURATED;
```

This only has to be done once per account; the values are stable unless the
external volume is re-created.

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

The credentials are published above. The other settings (`snowflake_account`,
`snowflake_user`, `snowflake_role`, `snowflake_warehouse`) are non-secret and
live in `pipelineConfig` and the Cube and Streamlit chart values — keep them in
sync with those outputs. There's no `OHDP_SNOWFLAKE_DATABASE` setting: the
catalog is named after `ohdp_ingestion.naming.CATALOG`, so its name is fixed
rather than passed through the environment (ADR-0019).

`snowflake_role` is not only a SQL setting: it is half of Horizon's OAuth2
scope (`session:role:<role>`), which Snowflake's token endpoint rejects the
exchange without. If the pipeline ever comes back with `invalid_scope`, that
mismatch is the first thing to check — it is what ADR-0012 lost a round to.

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
- **`terraform destroy` drops the RAW/CLEAN/CURATED databases** — the tables,
  not just a view of them, and the S3 bucket with them. Nothing here is backed up
  (ARCHITECTURE.md §11): the lakehouse rebuilds from public sources plus git.
- **The PAT expires.** Up to 365 days (`snowflake_pat_days_to_expiry`), unlike
  the RSA key pair, which has no expiry. Rotate with
  `terraform apply -replace=snowflake_user_programmatic_access_token.pipeline`,
  then re-run the `gh secret set SNOWFLAKE_PIPELINE_PAT` step and redeploy.
- **The network policy is a PAT prerequisite, not decoration.** Snowflake
  refuses token auth for a user with no network policy, so removing it breaks
  the whole pipeline rather than merely widening access.
- **Identifiers are upper case everywhere.** Horizon requires an external
  engine to address the database, namespaces and tables in all capitals, so
  `ohdp_ingestion.naming`, dbt's schema/alias macros and dlt's naming
  convention (`ohdp_ingestion.sql_upper`) all agree on it. A lower-case name
  anywhere in that chain is a bug, not a style choice.
