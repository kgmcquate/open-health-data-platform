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
  description = "Spaces bucket holding snapshots and backups."
  value       = digitalocean_spaces_bucket.warehouse.name
}

# ---------------------------------------------------------------------------
# Snowflake Horizon Catalog (ADR-0011). These four are exactly the OHDP_ICEBERG_*
# settings the pipeline reads; the first three are non-secret and live in the
# `ohdp-pipeline-config` ConfigMap, the credential is a repo secret.
# ---------------------------------------------------------------------------
output "iceberg_catalog_uri" {
  description = "OHDP_ICEBERG_CATALOG_URI — the Horizon Catalog Iceberg REST endpoint."
  value       = local.snowflake_catalog_uri
}

output "iceberg_warehouse" {
  description = "OHDP_ICEBERG_WAREHOUSE — the Snowflake database a REST client attaches to."
  value       = snowflake_database.ohdp.name
}

output "iceberg_scope" {
  description = "OHDP_ICEBERG_SCOPE — the OAuth2 scope naming the role the catalog session runs as."
  value       = "session:role:${snowflake_account_role.pipeline.name}"
}

output "iceberg_credential" {
  description = <<-EOT
    OHDP_ICEBERG_CREDENTIAL — the bare PAT pyiceberg exchanges for an access
    token as client_secret. NOT "<user>:<pat>": Snowflake's token endpoint
    400s with invalid_scope if a client_id rides along. Store it as the repo
    secret SNOWFLAKE_ICEBERG_CREDENTIAL, which deploy-platform.yml reads.
  EOT
  value       = local.iceberg_credential
  sensitive   = true
}

output "iceberg_credential_command" {
  description = "Push the catalog credential straight into the GitHub repo secret the deploy reads."
  value       = "terraform output -raw iceberg_credential | gh secret set SNOWFLAKE_ICEBERG_CREDENTIAL"
}
