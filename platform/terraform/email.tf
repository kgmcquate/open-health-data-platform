# Inbound mail for the addresses the site publishes (Privacy, Terms, Support,
# footer). Cloudflare Email Routing only forwards — there is no mailbox here,
# and replies go out from the destination inbox, not from these addresses.
#
# Enabling routing (`cloudflare_email_routing_settings`) is what adds and locks
# the zone's MX and SPF records; they are Cloudflare-managed, so they are not
# declared as `cloudflare_dns_record`s in dns.tf.
#
# The destination address must be verified before any rule delivers to it:
# the first apply makes Cloudflare send a verification email to it, and mail
# to the routed addresses is dropped until that link is clicked. The API token
# needs Zone Settings: Edit, Email Routing Rules: Edit, and (account-level)
# Email Routing Addresses: Edit, on top of the DNS scopes dns.tf already uses.

data "cloudflare_zone" "main" {
  zone_id = var.cloudflare_zone_id
}

resource "cloudflare_email_routing_settings" "main" {
  zone_id = var.cloudflare_zone_id
}

# Destination addresses are account-scoped, not zone-scoped — hence the zone
# lookup above rather than a second ID variable.
resource "cloudflare_email_routing_address" "destination" {
  account_id = data.cloudflare_zone.main.account.id
  email      = var.email_forward_to
}

resource "cloudflare_email_routing_rule" "forward" {
  for_each = toset(var.email_routed_local_parts)

  zone_id = var.cloudflare_zone_id
  name    = "${each.value}@${var.dns_base} -> ${var.email_forward_to}"
  enabled = true

  matchers = [{
    type  = "literal"
    field = "to"
    value = "${each.value}@${var.dns_base}"
  }]
  actions = [{
    type  = "forward"
    value = [cloudflare_email_routing_address.destination.email]
  }]

  depends_on = [cloudflare_email_routing_settings.main]
}
