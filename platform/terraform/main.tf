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

# Always-on serving path: hub-api, cube, cubestore and the platform
# Postgres they all read from. Kept off `heavy` so hand-scaling that pool for
# Dagster never takes the user-facing API or its dependencies with it. Pods
# are placed here by `nodeSelector: {node-role: services}` in each chart's
# values — no taint, so an unpinned pod can still land here.
# Sized for ~1.5Gi of requests plus hub-api's rolling-update surge pod.
# (mcp-cube, also on the serving path, sits on the default/ingress pool.)
resource "digitalocean_kubernetes_node_pool" "np_services" {
  cluster_id = digitalocean_kubernetes_cluster.cluster.id
  name       = "${var.name}-services"
  size       = "s-2vcpu-4gb"
  node_count = 1
  auto_scale = false
  labels = {
    node-role = "services"
  }

  lifecycle {
    ignore_changes = [node_count]
  }
}

# resource "digitalocean_kubernetes_node_pool" "np_8_cpu_16gb" {
#   cluster_id = digitalocean_kubernetes_cluster.cluster.id
#   name       = "${var.name}-8-cpu-16gb"
#   size       = "s-8vcpu-16gb"
#   node_count = 0
#   auto_scale = false
#   labels = {
#     node-role = "heavy"
#   }

#   lifecycle {
#     ignore_changes = [node_count]
#   }
# }

resource "digitalocean_kubernetes_node_pool" "np_4_cpu_8gb" {
  cluster_id = digitalocean_kubernetes_cluster.cluster.id
  name       = "${var.name}-4-cpu-8gb"
  size       = "s-4vcpu-8gb"
  node_count = 0
  auto_scale = false
  labels = {
    node-role = "heavy"
  }

  lifecycle {
    ignore_changes = [node_count]
  }
}
