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
    The Cloudflare A records (DNS-only) are created only once `loadbalancer_ip`
    is set. After deploy-platform brings Traefik up, read the load balancer IP
    with this command, set it as `loadbalancer_ip` (tfvars or
    TF_VAR_loadbalancer_ip), and re-apply this stack:
      kubectl -n infra get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
  EOT
  value       = "kubectl -n infra get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'"
}

output "service_hostnames" {
  description = "Fully-qualified hostnames managed as Cloudflare A records (empty until loadbalancer_ip is set)."
  value       = [for r in cloudflare_dns_record.service : r.name]
}

output "warehouse_bucket" {
  description = "Spaces bucket holding snapshots and backups."
  value       = digitalocean_spaces_bucket.warehouse.name
}
