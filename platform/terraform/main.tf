locals {
  node_name = "${var.name}-node"

  # Only Cloudflare may reach 80/443. This is what makes the WAF and rate
  # limiting in ARCHITECTURE.md §5 actually enforceable — without it anyone can
  # hit the origin IP directly and skip every edge control.
  cloudflare_cidrs = concat(
    data.cloudflare_ip_ranges.cloudflare.ipv4_cidr_blocks,
    data.cloudflare_ip_ranges.cloudflare.ipv6_cidr_blocks,
  )
}

data "cloudflare_ip_ranges" "cloudflare" {}

resource "hcloud_ssh_key" "admin" {
  name       = "${var.name}-admin"
  public_key = var.ssh_public_key
}

resource "hcloud_primary_ip" "ipv4" {
  name        = "${var.name}-ipv4"
  type        = "ipv4"
  location    = var.location
  auto_delete = false

  lifecycle {
    # Keep the address across server rebuilds so DNS does not have to change.
    prevent_destroy = true
  }
}

resource "hcloud_firewall" "node" {
  name = "${var.name}-node"

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.admin_ip_ranges
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "6443"
    source_ips  = var.admin_ip_ranges
    description = "Kubernetes API — admin networks only"
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "80"
    source_ips  = local.cloudflare_cidrs
    description = "HTTP from Cloudflare edge only"
  }

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "443"
    source_ips  = local.cloudflare_cidrs
    description = "HTTPS from Cloudflare edge only"
  }

  rule {
    direction   = "in"
    protocol    = "icmp"
    source_ips  = ["0.0.0.0/0", "::/0"]
    description = "ping"
  }
}

resource "hcloud_server" "node" {
  name         = local.node_name
  server_type  = var.server_type
  location     = var.location
  image        = "ubuntu-24.04"
  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.node.id]

  public_net {
    ipv4_enabled = true
    ipv4         = hcloud_primary_ip.ipv4.id
    ipv6_enabled = true
  }

  user_data = templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {
    k3s_version = var.k3s_version
    tls_sans    = concat([hcloud_primary_ip.ipv4.ip_address], [for s in var.subdomains : "${s}.${var.domain}"])
  })

  labels = {
    project = var.name
    role    = "k3s-server"
  }

  lifecycle {
    # user_data changes would otherwise force a rebuild and wipe the cluster.
    # Re-run bootstrap by hand instead, or take the rebuild deliberately.
    ignore_changes = [user_data, image]
  }
}

# Single VM, single replica, no HA (ARCHITECTURE.md §1.4, §9). There is
# deliberately no load balancer, no second node, and no autoscaling here.
