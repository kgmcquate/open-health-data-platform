# All records are proxied (orange cloud). That is what puts Cloudflare's WAF and
# rate limiting in front of the origin (ARCHITECTURE.md §5) — and the firewall in
# main.tf only admits Cloudflare ranges on 80/443, so an unproxied record would
# simply stop resolving to a reachable host.
#
# Because the origin is closed to the internet, use the DNS-01 ACME challenge in
# cert-manager (it has a Cloudflare token already), not HTTP-01.

resource "digitalocean_record" "subdomain" {
  for_each = var.enable_dns ? toset(var.subdomains) : toset([])

  domain = var.domain
  type   = "A"
  name   = each.value
  value  = digitalocean_kubernetes_cluster.cluster.ipv4_address
  ttl    = 60
}

resource "digitalocean_record" "apex" {
  count = var.enable_dns ? 1 : 0

  domain = var.domain
  type   = "A"
  name   = "@"
  value  = digitalocean_kubernetes_cluster.cluster.ipv4_address
  ttl    = 60
}
