locals {
  node_name = "${var.name}-node"

  # Only Cloudflare may reach 80/443 — that is what makes the §5 WAF and rate
  # limiting unbypassable. Kamatera has no cloud firewall resource (Hetzner had
  # hcloud_firewall), so those rules live on the host as nftables, rendered into
  # the startup script from the Cloudflare data source and refreshed daily by a
  # systemd timer (the startup script itself runs only once).

  # The Kubernetes API (6443) cannot be Cloudflare-proxied on the free plan, so
  # it is reached directly at k3s.<domain> (an unproxied record, see dns.tf),
  # gated by admin_ip_ranges in nftables. k3s needs this name in its serving
  # cert. The apex and proxied subdomains are here too so an in-cluster client
  # hitting them by name still verifies.
  api_hostname = "k3s.${var.domain}"
  tls_sans = concat(
    [local.api_hostname, var.domain],
    [for s in var.subdomains : "${s}.${var.domain}"],
  )
}

data "cloudflare_ip_ranges" "cloudflare" {}

data "kamatera_datacenter" "node" {
  country = var.datacenter_country
  name    = var.datacenter_name
}

data "kamatera_image" "ubuntu" {
  datacenter_id = data.kamatera_datacenter.node.id
  os            = "Ubuntu"
  code          = var.image_code
}

# Single VM, single replica, no HA (ARCHITECTURE.md §1.4, §9). There is
# deliberately no load balancer, no second node, and no autoscaling here.
resource "kamatera_server" "node" {
  name          = local.node_name
  datacenter_id = data.kamatera_datacenter.node.id
  image_id      = data.kamatera_image.ubuntu.id

  cpu_type      = var.cpu_type
  cpu_cores     = var.cpu_cores
  ram_mb        = var.ram_mb
  disk_sizes_gb = [var.disk_size_gb]
  billing_cycle = var.billing_cycle

  ssh_pubkey = var.ssh_public_key

  network {
    name = "wan"
    ip   = "auto"
  }

  # nftables (admin_ip_ranges -> 22/6443, Cloudflare -> 80/443), sysctl for
  # OpenSearch, then k3s. Replaces Hetzner's cloud-init + hcloud_firewall.
  startup_script = templatefile("${path.module}/templates/startup-script.sh.tftpl", {
    k3s_version   = var.k3s_version
    tls_sans      = local.tls_sans
    admin_v4      = [for c in var.admin_ip_ranges : c if !strcontains(c, ":")]
    admin_v6      = [for c in var.admin_ip_ranges : c if strcontains(c, ":")]
    cloudflare_v4 = data.cloudflare_ip_ranges.cloudflare.ipv4_cidr_blocks
    cloudflare_v6 = data.cloudflare_ip_ranges.cloudflare.ipv6_cidr_blocks
  })

  lifecycle {
    # A startup_script or image change would force a rebuild and wipe the
    # cluster. Re-run bootstrap by hand instead, or take the rebuild
    # deliberately (terraform taint).
    ignore_changes = [startup_script, image_id]
  }
}
