# All records are proxied (orange cloud). That is what puts Cloudflare's WAF and
# rate limiting in front of the origin (ARCHITECTURE.md §5) — and the firewall in
# main.tf only admits Cloudflare ranges on 80/443, so an unproxied record would
# simply stop resolving to a reachable host.
#
# Because the origin is closed to the internet, use the DNS-01 ACME challenge in
# cert-manager (it has a Cloudflare token already), not HTTP-01.

resource "cloudflare_record" "subdomain" {
  for_each = var.enable_dns ? toset(var.subdomains) : toset([])

  zone_id = var.cloudflare_zone_id
  name    = each.value
  content = hcloud_primary_ip.ipv4.ip_address
  type    = "A"
  ttl     = 1 # 1 = automatic; required when proxied
  proxied = true
  comment = "Managed by Terraform — ${var.name}"
}

resource "cloudflare_record" "apex" {
  count = var.enable_dns ? 1 : 0

  zone_id = var.cloudflare_zone_id
  name    = "@"
  content = hcloud_primary_ip.ipv4.ip_address
  type    = "A"
  ttl     = 1
  proxied = true
  comment = "Managed by Terraform — ${var.name}"
}
