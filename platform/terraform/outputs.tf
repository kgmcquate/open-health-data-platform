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

output "warehouse_bucket" {
  description = "Spaces bucket holding snapshots and backups."
  value       = digitalocean_spaces_bucket.warehouse.name
}

output "monthly_cost_estimate_usd" {
  description = "Rough fixed infrastructure cost."
  value       = "${var.server_type} x ${var.node_count} nodes in ${var.location}; check https://www.digitalocean.com/pricing for current pricing"
}
