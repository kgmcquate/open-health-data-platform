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
