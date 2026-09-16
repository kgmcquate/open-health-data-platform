# Platform hostnames. open-health-data-platform.org is a Cloudflare zone, so the
# records live there rather than in DigitalOcean DNS.
#
# DNS-only (proxied = false): Cloudflare is not in the request path, so TLS stays
# at the origin (cert-manager HTTP-01 through Traefik). Flipping a record to
# proxied later also means moving cert-manager to a DNS-01 solver or a
# Cloudflare Origin CA cert.
#
# The Cloudflare A records point at the Traefik public IP configured in
# `loadbalancer_ip`; do not rely on a DigitalOcean reserved IP here.

resource "cloudflare_dns_record" "service" {
  for_each = toset(var.dns_hostnames)

  zone_id = var.cloudflare_zone_id
  name    = "${each.value}.${var.dns_base}"
  type    = "A"
  content = var.loadbalancer_ip
  ttl     = 60
  proxied = false
}
