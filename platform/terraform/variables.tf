variable "domain" {
  description = "Apex domain serving the platform, e.g. example.com."
  type        = string
}

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

variable "enable_dns" {
  description = "Manage A records for the platform hostnames in DigitalOcean DNS."
  type        = bool
  default     = true
}

variable "subdomains" {
  description = "Hostnames fronted by the ingress."
  type        = list(string)
  default     = ["app", "dagster", "superset", "catalog", "cube"]
}
