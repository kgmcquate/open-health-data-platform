output "node_ipv4" {
  description = "Public IPv4 of the k3s node."
  value       = kamatera_server.node.public_ips[0]
}

output "api_hostname" {
  description = "Hostname the Kubernetes API is reached at (unproxied A record, port 6443)."
  value       = local.api_hostname
}

output "ssh_command" {
  description = "SSH to the node (only from admin_ip_ranges)."
  value       = "ssh root@${kamatera_server.node.public_ips[0]}"
}

output "fetch_kubeconfig" {
  description = "Pull the cluster kubeconfig and rewrite its server address to the API hostname."
  value = join(" ", [
    "ssh root@${kamatera_server.node.public_ips[0]} 'cat /etc/rancher/k3s/k3s.yaml'",
    "| sed 's#https://127.0.0.1:6443#https://${local.api_hostname}:6443#'",
    "> platform/terraform/kubeconfig && chmod 600 platform/terraform/kubeconfig",
  ])
}

output "warehouse_bucket" {
  description = "R2 bucket holding snapshots and backups."
  value       = cloudflare_r2_bucket.warehouse.name
}
