# The apex and every subdomain are proxied (orange cloud). That is what puts
# Cloudflare's WAF and rate limiting in front of the origin (ARCHITECTURE.md §5)
# — and the host firewall in the startup script only admits Cloudflare ranges on
# 80/443, so an unproxied app record would simply stop resolving to a reachable
# host.
#
# Because the origin is closed to the internet, use the DNS-01 ACME challenge in
# cert-manager (it has a Cloudflare token already), not HTTP-01.
#
# The one exception is `k3s.<domain>` below: the Kubernetes API (6443) cannot be
# Cloudflare-proxied on the free plan, so that record is unproxied and points
# straight at the node. nftables still gates it to admin_ip_ranges.

locals {
  node_ipv4 = kamatera_server.node.public_ips[0]
}

resource "cloudflare_record" "subdomain" {
  for_each = var.enable_dns ? toset(var.subdomains) : toset([])

  zone_id = var.cloudflare_zone_id
  name    = each.value
  content = local.node_ipv4
  type    = "A"
  ttl     = 1 # 1 = automatic; required when proxied
  proxied = true
  comment = "Managed by Terraform — ${var.name}"
}

resource "cloudflare_record" "apex" {
  count = var.enable_dns ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = "@"
  content = local.node_ipv4
  type    = "A"
  ttl     = 1
  proxied = true
  comment = "Managed by Terraform — ${var.name}"
}

resource "cloudflare_record" "k3s_api" {
  count = var.enable_dns ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = "k3s"
  content = local.node_ipv4
  type    = "A"
  ttl     = 300
  proxied = false # 6443 is not proxyable; nftables restricts it to admins
  comment = "Managed by Terraform — ${var.name} — Kubernetes API"
}
