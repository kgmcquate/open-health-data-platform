output "node_ipv4" {
  description = "Public IPv4 of the k3s node."
  value       = hcloud_primary_ip.ipv4.ip_address
}

output "ssh_command" {
  description = "SSH to the node (only from admin_ip_ranges)."
  value       = "ssh root@${hcloud_primary_ip.ipv4.ip_address}"
}

output "fetch_kubeconfig" {
  description = "Pull the cluster kubeconfig and rewrite its server address."
  value = join(" ", [
    "ssh root@${hcloud_primary_ip.ipv4.ip_address} 'cat /etc/rancher/k3s/k3s.yaml'",
    "| sed 's#127.0.0.1#${hcloud_primary_ip.ipv4.ip_address}#'",
    "> platform/terraform/kubeconfig && chmod 600 platform/terraform/kubeconfig",
  ])
}

output "warehouse_bucket" {
  description = "R2 bucket holding snapshots and backups."
  value       = cloudflare_r2_bucket.warehouse.name
}

output "monthly_cost_estimate_eur" {
  description = "Rough fixed infrastructure cost. Compare against the ~EUR 33 ceiling in ARCHITECTURE.md §4."
  value       = "${var.server_type} in ${var.location} + primary IPv4; check https://www.hetzner.com/cloud for current pricing"
}
