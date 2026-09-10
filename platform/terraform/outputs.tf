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
    DNS is not managed here — kevinmcquate.com is a Cloudflare zone and the
    records are created by hand (DNS-only / grey cloud). After the platform is
    deployed, read the Traefik load balancer's public IP and point the
    *.ohdp.kevinmcquate.com A records at it:
      kubectl -n infra get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
  EOT
  value       = "kubectl -n infra get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'"
}

output "warehouse_bucket" {
  description = "Spaces bucket holding snapshots and backups."
  value       = digitalocean_spaces_bucket.warehouse.name
}

output "monthly_cost_estimate_usd" {
  description = "Rough fixed infrastructure cost."
  value       = "${var.server_type} x ${var.node_count} nodes in ${var.location}; check https://www.digitalocean.com/pricing for current pricing"
}
