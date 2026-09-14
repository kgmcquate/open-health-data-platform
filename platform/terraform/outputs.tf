output "cluster_id" {
  description = "ID of the managed DigitalOcean Kubernetes cluster."
  value       = digitalocean_kubernetes_cluster.cluster.id
}

output "cluster_endpoint" {
  description = "Public endpoint for the managed Kubernetes API."
  value       = digitalocean_kubernetes_cluster.cluster.endpoint
}

output "cluster_ipv4" {
  description = "Public IPv4 for the managed Kubernetes cluster."
  value       = digitalocean_kubernetes_cluster.cluster.ipv4_address
}

output "kubeconfig" {
  description = "Raw kubeconfig for the managed cluster."
  value       = digitalocean_kubernetes_cluster.cluster.kube_config[0].raw_config
  sensitive   = true
}

output "fetch_kubeconfig" {
  description = "Write the managed cluster kubeconfig to a local file."
  value       = "terraform output -raw kubeconfig > platform/terraform/kubeconfig && chmod 600 platform/terraform/kubeconfig"
}

output "loadbalancer_ip_command" {
  description = <<-EOT
    Set `loadbalancer_ip` directly in terraform.tfvars (or TF_VAR_loadbalancer_ip)
    to the public IPv4 for the Traefik ingress. Do not rely on a reserved IP or
    a fallback value here.
  EOT
  value       = "Set loadbalancer_ip in terraform.tfvars or TF_VAR_loadbalancer_ip to the Traefik public IPv4."
}

output "service_hostnames" {
  description = "Fully-qualified hostnames managed as Cloudflare A records."
  value       = [for r in cloudflare_dns_record.service : r.name]
}

output "warehouse_bucket" {
  description = "Spaces bucket holding Postgres backups and the mirrored Snowflake credentials (private key + Horizon PAT)."
  value       = digitalocean_spaces_bucket.warehouse.name
}

# ---------------------------------------------------------------------------
# The Iceberg lakehouse (ADR-0019). Snowflake is the catalog; AWS only holds
# the files. Bucket and region are non-secret and live in the
# `ohdp-pipeline-config` ConfigMap; the IAM access key and the Snowflake PAT
# are repo secrets.
# ---------------------------------------------------------------------------
output "lakehouse_bucket" {
  description = "OHDP_LAKEHOUSE_BUCKET — S3 bucket behind the external volume."
  value       = aws_s3_bucket.lakehouse.bucket
}

output "compute_logs_bucket" {
  description = "Spaces bucket holding Dagster's S3ComputeLogManager output."
  value       = digitalocean_spaces_bucket.compute_logs.name
}

output "aws_region" {
  description = "OHDP_AWS_REGION — region of the external volume's bucket."
  value       = var.aws_region
}

output "horizon_catalog_uri" {
  description = <<-EOT
    Snowflake Horizon's Iceberg REST endpoint — what dlt (pyiceberg) and
    DuckDB's `ATTACH` both point at. Derived from the account identifier, so
    nothing has to carry it in config; shown here for debugging.
  EOT
  value       = local.horizon_catalog_uri
}

output "lakehouse_catalogs" {
  description = <<-EOT
    The medallion-layer databases (ADR-0013). Each is simultaneously a
    Snowflake database, an Iceberg catalog served over Horizon, and a DuckDB
    ATTACH alias.
  EOT
  value       = values(local.snowflake_layer_databases)
}

output "aws_pipeline_access_key_id" {
  description = <<-EOT
    OHDP_AWS_ACCESS_KEY_ID — the pipeline IAM user's access key. Needed for one
    job only: dlt writes each raw table's Parquet into the volume's bucket
    itself and cannot use the credentials Horizon vends. DuckDB does use those,
    and has no AWS key.
  EOT
  value       = aws_iam_access_key.pipeline.id
}

output "aws_pipeline_secret_access_key" {
  description = "OHDP_AWS_SECRET_ACCESS_KEY — the pipeline IAM user's secret key."
  value       = aws_iam_access_key.pipeline.secret
  sensitive   = true
}

output "aws_pipeline_key_commands" {
  description = "Publish the pipeline's AWS key into the repo secrets deploy-platform.yml reads."
  value = join("\n", [
    "terraform output -raw aws_pipeline_access_key_id | gh secret set AWS_PIPELINE_ACCESS_KEY_ID",
    "terraform output -raw aws_pipeline_secret_access_key | gh secret set AWS_PIPELINE_SECRET_ACCESS_KEY",
  ])
}

output "snowflake_pipeline_pat" {
  description = <<-EOT
    OHDP_SNOWFLAKE_PAT — the pipeline's programmatic access token for Horizon's
    Iceberg REST catalog. Store it as the repo secret SNOWFLAKE_PIPELINE_PAT,
    which deploy-platform.yml reads. Expires; see snowflake_pat_days_to_expiry.
  EOT
  value       = snowflake_user_programmatic_access_token.pipeline.token
  sensitive   = true
}

output "snowflake_pipeline_pat_command" {
  description = "Push the catalog token straight into the GitHub repo secret the deploy reads."
  value       = "terraform output -raw snowflake_pipeline_pat | gh secret set SNOWFLAKE_PIPELINE_PAT"
}

output "snowflake_trust_policy_inputs" {
  description = <<-EOT
    Second-apply inputs (ADR-0019). Snowflake generates an IAM user ARN and an
    external ID when it creates the external volume; the AWS role it assumes
    can only trust them afterwards. Read them out and apply again:

      DESC EXTERNAL VOLUME <volume>;  -> STORAGE_AWS_IAM_USER_ARN, STORAGE_AWS_EXTERNAL_ID

    then set TF_VAR_snowflake_storage_aws_iam_user_arn and
    TF_VAR_snowflake_storage_aws_external_id.
  EOT
  value = {
    external_volume = local.snowflake_external_volume
  }
}

# ---------------------------------------------------------------------------
# Snowflake connection (ADR-0019). The OHDP_SNOWFLAKE_* settings:
# account/user/role/warehouse are non-secret and live in Helm values; the
# private key (SQL) and the PAT (REST, above) are repo secrets. There is no
# OHDP_SNOWFLAKE_DATABASE: the catalog's name is fixed
# (ohdp_ingestion.naming.CATALOG), not passed through the environment.
# ---------------------------------------------------------------------------
output "snowflake_account" {
  description = "OHDP_SNOWFLAKE_ACCOUNT — the <organization>-<account> identifier, for both SQL and the REST endpoint."
  value       = local.snowflake_account_identifier
}

output "snowflake_user" {
  description = "OHDP_SNOWFLAKE_USER — the service user everything connects as; also the OAuth2 client id."
  value       = snowflake_service_user.pipeline.name
}

output "snowflake_role" {
  description = <<-EOT
    OHDP_SNOWFLAKE_ROLE — the role every session runs as. Also half of
    Horizon's OAuth2 scope (`session:role:<this>`), so the pipeline reads it
    too, not just Cube and Streamlit.
  EOT
  value       = snowflake_account_role.pipeline.name
}

output "snowflake_warehouse" {
  description = "OHDP_SNOWFLAKE_WAREHOUSE — the virtual warehouse their queries run on."
  value       = snowflake_warehouse.ohdp.name
}



output "snowflake_private_key" {
  description = <<-EOT
    OHDP_SNOWFLAKE_PRIVATE_KEY — the query role's RSA private key (PKCS#8 PEM),
    read by Cube's and Streamlit's Snowflake drivers. Store it as the repo
    secret SNOWFLAKE_PIPELINE_PRIVATE_KEY, which deploy-platform.yml reads.
  EOT
  value       = local.snowflake_private_key
  sensitive   = true
}

output "snowflake_private_key_command" {
  description = "Push the Snowflake private key straight into the GitHub repo secret the deploy reads."
  value       = "terraform output -raw snowflake_private_key | gh secret set SNOWFLAKE_PIPELINE_PRIVATE_KEY"
}
