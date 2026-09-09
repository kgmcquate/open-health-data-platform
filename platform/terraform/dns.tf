# A records pointing straight at the DOKS load balancer IP. The origin is
# reachable on 80/443, so cert-manager uses the HTTP-01 ACME challenge over the
# Traefik ingress (platform/k3s/base/cluster-issuer.yaml).
#
# NOTE: these are DigitalOcean DNS records, not Cloudflare. There is no edge WAF
# in front of the origin on DOKS — see platform/terraform/README.md.

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
