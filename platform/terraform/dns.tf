locals {
  traefik_loadbalancer_ip = var.loadbalancer_ip != "" ? var.loadbalancer_ip : digitalocean_reserved_ip.traefik.ip_address
}

# Platform hostnames. kevinmcquate.com is a Cloudflare zone, so the records live
# there rather than in DigitalOcean DNS.
#
# DNS-only (proxied = false): Cloudflare is not in the request path, so TLS stays
# Let's Encrypt at the origin (cert-manager HTTP-01 through Traefik). Flipping a
# record to proxied later also means moving cert-manager to a DNS-01 solver or a
# Cloudflare Origin CA cert.
#
# The Traefik service should use a reserved DigitalOcean IP so we can scale the
# large pool down to zero without changing the public IP or the DNS target.

resource "cloudflare_dns_record" "service" {
  for_each = toset(var.dns_hostnames)

  zone_id = var.cloudflare_zone_id
  name    = "${each.value}.${var.dns_base}"
  type    = "A"
  content = local.traefik_loadbalancer_ip
  ttl     = 60
  proxied = false
}
