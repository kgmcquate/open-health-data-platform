locals {
  cluster_name = "${var.name}-cluster"
}

resource "digitalocean_kubernetes_cluster" "cluster" {
  name    = local.cluster_name
  region  = var.location
  version = var.kubernetes_version == "" ? null : var.kubernetes_version
  tags    = [var.name]
  ha      = false

  node_pool {
    name       = "${var.name}-default"
    size       = "s-1vcpu-2gb"
    node_count = 1
    labels = {
      node-role = "ingress"
    }
  }
}

resource "digitalocean_kubernetes_node_pool" "8_cpu_16gb" {
  cluster_id = digitalocean_kubernetes_cluster.cluster.id
  name       = "${var.name}-8-cpu-16gb"
  size       = "s-8vcpu-16gb"
  node_count = 0
  auto_scale = false
  labels = {
    node-role = "heavy"
  }

  lifecycle {
    ignore_changes = [ node_count ]
  }
}

resource "digitalocean_kubernetes_node_pool" "4_cpu_8gb" {
  cluster_id = digitalocean_kubernetes_cluster.cluster.id
  name       = "${var.name}-4-cpu-8gb"
  size       = "s-4vcpu-8gb"
  node_count = 0
  auto_scale = false
  labels = {
    node-role = "heavy"
  }

  lifecycle {
    ignore_changes = [ node_count ]
  }
}
