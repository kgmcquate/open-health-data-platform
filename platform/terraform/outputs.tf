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
  description = "Spaces bucket holding Postgres backups and the mirrored Snowflake pipeline key."
  value       = digitalocean_spaces_bucket.warehouse.name
}

# ---------------------------------------------------------------------------
# Snowflake connection (ADR-0012). These are exactly the OHDP_SNOWFLAKE_*
# settings the pipeline reads; account/user/role/warehouse/database are
# non-secret and live in the `ohdp-pipeline-config` ConfigMap, the password is
# a repo secret.
# ---------------------------------------------------------------------------
output "snowflake_account" {
  description = "OHDP_SNOWFLAKE_ACCOUNT — the <organization>-<account> identifier dlt/dbt-snowflake connect to."
  value       = local.snowflake_account_identifier
}

output "snowflake_user" {
  description = "OHDP_SNOWFLAKE_USER — the service user the pipeline connects as."
  value       = snowflake_service_user.pipeline.name
}

output "snowflake_role" {
  description = "OHDP_SNOWFLAKE_ROLE — the role the pipeline's session runs as."
  value       = snowflake_account_role.pipeline.name
}

output "snowflake_warehouse" {
  description = "OHDP_SNOWFLAKE_WAREHOUSE — the virtual warehouse (compute) for dlt loads and dbt-snowflake builds."
  value       = snowflake_warehouse.ohdp.name
}

output "snowflake_database" {
  description = "OHDP_SNOWFLAKE_DATABASE — the Snowflake database holding the medallion schemas."
  value       = snowflake_database.ohdp.name
}

output "snowflake_private_key" {
  description = <<-EOT
    OHDP_SNOWFLAKE_PRIVATE_KEY — the pipeline's RSA private key (PKCS#8 PEM),
    read directly by both dlt's Snowflake destination and dbt-snowflake's
    profile. Store it as the repo secret SNOWFLAKE_PIPELINE_PRIVATE_KEY, which
    deploy-platform.yml reads.
  EOT
  value       = local.snowflake_private_key
  sensitive   = true
}

output "snowflake_private_key_command" {
  description = "Push the pipeline private key straight into the GitHub repo secret the deploy reads."
  value       = "terraform output -raw snowflake_private_key | gh secret set SNOWFLAKE_PIPELINE_PRIVATE_KEY"
}
