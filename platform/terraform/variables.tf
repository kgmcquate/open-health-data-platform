variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "ohdp"
}

variable "location" {
  description = <<-EOT
    DigitalOcean region for the cluster and Spaces bucket. Use a region close to
    your users (for example fra1 for Frankfurt or nyc3 for New York).
  EOT
  type        = string
  default     = "nyc3"

  validation {
    condition     = contains(["ams3", "blr1", "fra1", "lon1", "nyc1", "nyc2", "nyc3", "sfo2", "sfo3", "sgp1", "syd1", "tor1"], var.location)
    error_message = "location must be a valid DigitalOcean region."
  }
}

variable "server_type" {
  description = "DigitalOcean Kubernetes node size for the default pool."
  type        = string
  default     = "s-4vcpu-8gb"
}

variable "kubernetes_version" {
  description = "Pinned Kubernetes version, or empty to let DigitalOcean pick the default supported version."
  type        = string
  default     = "1.36.3-do.4"
}

variable "node_count" {
  description = "Number of worker nodes in the default pool."
  type        = number
  default     = 1
}

variable "snapshot_bucket" {
  description = "DigitalOcean Spaces bucket for versioned DuckDB snapshots and Postgres backups."
  type        = string
  default     = "ohdp-warehouse"
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for the domain hosting the platform hostnames."
  type        = string
}

variable "dns_base" {
  description = "Base under which per-service A records are created, e.g. ohdp.kevinmcquate.com -> app.ohdp.kevinmcquate.com."
  type        = string
  default     = "ohdp.kevinmcquate.com"
}

variable "dns_hostnames" {
  description = "Service hostnames (left-most label) fronted by the Traefik ingress."
  type        = list(string)
  default     = ["app", "dagster", "catalog", "cube", "superset"]
}

variable "loadbalancer_ip" {
  description = <<-EOT
    Public IPv4 of the DigitalOcean load balancer Traefik provisions during
    deploy-platform. Empty on the first infra apply (the LB does not exist yet),
    which skips the DNS records; set it and re-apply once the platform is up.
      kubectl -n infra get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
  EOT
  type        = string
  default     = ""
}
