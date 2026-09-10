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
    size       = "s-1vcpu-1gb"
    node_count = 1
  }
}

resource "digitalocean_kubernetes_node_pool" "heavy" {
  cluster_id = digitalocean_kubernetes_cluster.cluster.id
  name       = "${var.name}-heavy"
  size       = "s-8vcpu-16gb"
  node_count = 1
}

# Single managed Kubernetes cluster, no self-hosted k3s bootstrap on a droplet.
