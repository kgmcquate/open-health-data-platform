locals {
  cluster_name = "${var.name}-cluster"
}

resource "digitalocean_kubernetes_cluster" "cluster" {
  name    = local.cluster_name
  region  = var.location
  version = var.kubernetes_version == "" ? null : var.kubernetes_version
  tags    = [var.name]

  node_pool {
    name       = "${var.name}-default"
    size       = var.server_type
    node_count = var.node_count
  }
}

# Single managed Kubernetes cluster, no self-hosted k3s bootstrap on a droplet.
