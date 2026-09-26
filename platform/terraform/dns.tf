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

# The bare domain (and www) redirect to the app. Unlike the service records
# these are proxied: the redirect rule below runs at Cloudflare's edge, so the
# request never reaches an origin and 192.0.2.1 (TEST-NET-1) is only a
# placeholder that lets the record be proxied. Cloudflare's Universal SSL cert
# covers the apex and www, so nothing changes for cert-manager.
resource "cloudflare_dns_record" "redirect" {
  for_each = toset([var.dns_base, "www.${var.dns_base}"])

  zone_id = var.cloudflare_zone_id
  name    = each.value
  type    = "A"
  content = "192.0.2.1"
  ttl     = 1 # "automatic" — required for proxied records
  proxied = true
}

# A zone allows one ruleset per phase; if a redirect rule was ever created in
# the dashboard, import that ruleset instead of creating this one.
resource "cloudflare_ruleset" "apex_redirect" {
  zone_id = var.cloudflare_zone_id
  name    = "Apex to app"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [{
    ref         = "apex_to_app"
    description = "Redirect ${var.dns_base} and www to app.${var.dns_base}"
    expression  = "(http.host in {\"${var.dns_base}\" \"www.${var.dns_base}\"})"
    action      = "redirect"
    action_parameters = {
      from_value = {
        # 302 while the apex has no home of its own; browsers cache 301s
        # indefinitely, which would make a later landing page hard to roll out.
        status_code           = 302
        preserve_query_string = true
        target_url = {
          expression = "concat(\"https://app.${var.dns_base}\", http.request.uri.path)"
        }
      }
    }
  }]
}
