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
  default     = ["app", "dagster", "catalog", "cube", "superset", "polaris"]
}

variable "loadbalancer_ip" {
  description = <<-EOT
    Public IPv4 for the Traefik ingress that fronts the platform services.
    Set this explicitly in terraform.tfvars (or TF_VAR_loadbalancer_ip) and
    keep it in sync with the actual Kuberenetes load balancer address.
  EOT
  type        = string
  default     = "134.199.244.105"

  validation {
    condition     = var.loadbalancer_ip == "" || can(cidrhost("${var.loadbalancer_ip}/32", 0))
    error_message = "loadbalancer_ip must be a valid IPv4 address or left empty."
  }
}
