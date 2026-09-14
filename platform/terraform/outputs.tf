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
  description = "Spaces bucket holding Postgres backups and the mirrored Snowflake private key."
  value       = digitalocean_spaces_bucket.warehouse.name
}

# ---------------------------------------------------------------------------
# The Iceberg lakehouse (ADR-0019). Bucket/region/catalog id are non-secret and
# live in the `ohdp-pipeline-config` ConfigMap; the IAM access key is a pair of
# repo secrets.
# ---------------------------------------------------------------------------
output "lakehouse_bucket" {
  description = "OHDP_LAKEHOUSE_BUCKET — S3 bucket every Iceberg table's files live in."
  value       = aws_s3_bucket.lakehouse.bucket
}

output "compute_logs_bucket" {
  description = "S3 bucket holding Dagster's S3ComputeLogManager output."
  value       = aws_s3_bucket.compute_logs.bucket
}

output "aws_region" {
  description = "OHDP_AWS_REGION — region of the lakehouse bucket and the Glue catalog."
  value       = var.aws_region
}

output "glue_catalog_id" {
  description = <<-EOT
    OHDP_GLUE_CATALOG_ID — the AWS account ID, which is what Glue calls the
    account-level catalog. dbt's `attach` path, dlt's pyiceberg `warehouse` and
    Snowflake's REST_CONFIG.CATALOG_NAME are all this value.
  EOT
  value       = data.aws_caller_identity.current.account_id
}

output "glue_rest_uri" {
  description = "Glue's Iceberg REST endpoint — derived from aws_region, shown for debugging."
  value       = local.glue_rest_uri
}

output "aws_pipeline_access_key_id" {
  description = "OHDP_AWS_ACCESS_KEY_ID — the pipeline IAM user's access key."
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

output "snowflake_trust_policy_inputs" {
  description = <<-EOT
    Second-apply inputs (ADR-0019). Snowflake generates an IAM user ARN and an
    external ID when it creates the external volume and the catalog
    integration; the AWS roles it assumes can only trust them afterwards. Read
    them out and apply again:

      DESC EXTERNAL VOLUME <volume>;       -> STORAGE_AWS_IAM_USER_ARN, STORAGE_AWS_EXTERNAL_ID
      DESC CATALOG INTEGRATION <integration>;  -> API_AWS_IAM_USER_ARN, API_AWS_EXTERNAL_ID

    then set TF_VAR_snowflake_storage_aws_iam_user_arn,
    TF_VAR_snowflake_storage_aws_external_id,
    TF_VAR_snowflake_glue_aws_iam_user_arn and
    TF_VAR_snowflake_glue_aws_external_id.
  EOT
  value = {
    external_volume     = local.snowflake_external_volume
    catalog_integration = local.snowflake_catalog_integration
  }
}

# ---------------------------------------------------------------------------
# Snowflake connection (ADR-0019). Exactly the OHDP_SNOWFLAKE_* settings Cube
# and Streamlit read; account/user/role/warehouse are non-secret and live in
# Helm values, the private key is a repo secret. There is no
# OHDP_SNOWFLAKE_DATABASE: the catalog-linked database's name is fixed
# (ohdp_ingestion.naming.CATALOG), not passed through the environment.
# ---------------------------------------------------------------------------
output "snowflake_account" {
  description = "OHDP_SNOWFLAKE_ACCOUNT — the <organization>-<account> identifier Cube and Streamlit connect to."
  value       = local.snowflake_account_identifier
}

output "snowflake_user" {
  description = "OHDP_SNOWFLAKE_USER — the service user Cube and Streamlit connect as."
  value       = snowflake_service_user.pipeline.name
}

output "snowflake_role" {
  description = "OHDP_SNOWFLAKE_ROLE — the read-only role their sessions run as."
  value       = snowflake_account_role.pipeline.name
}

output "snowflake_warehouse" {
  description = "OHDP_SNOWFLAKE_WAREHOUSE — the virtual warehouse their queries run on."
  value       = snowflake_warehouse.ohdp.name
}

output "snowflake_linked_database" {
  description = "Catalog-linked database the lakehouse's namespaces appear under in Snowflake."
  value       = local.snowflake_linked_database
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
