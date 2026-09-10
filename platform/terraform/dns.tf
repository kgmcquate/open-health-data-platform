# Platform hostnames. kevinmcquate.com is a Cloudflare zone, so the records live
# there rather than in DigitalOcean DNS.
#
# DNS-only (proxied = false): Cloudflare is not in the request path, so TLS stays
# Let's Encrypt at the origin (cert-manager HTTP-01 through Traefik). Flipping a
# record to proxied later also means moving cert-manager to a DNS-01 solver or a
# Cloudflare Origin CA cert.
#
# Chicken-and-egg: the target is the DigitalOcean load balancer that Traefik
# provisions during `deploy-platform`, which runs after this. `loadbalancer_ip`
# is therefore empty on the first apply and these records are skipped; read the
# IP (see the `loadbalancer_ip_command` output), set it, and re-apply.

resource "cloudflare_dns_record" "service" {
  for_each = var.loadbalancer_ip == "" ? toset([]) : toset(var.dns_hostnames)

  zone_id = var.cloudflare_zone_id
  name    = "${each.value}.${var.dns_base}"
  type    = "A"
  content = var.loadbalancer_ip
  ttl     = 60
  proxied = false
}
