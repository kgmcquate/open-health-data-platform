variable "hcloud_token" {
  description = "Hetzner Cloud API token (project-scoped, read/write)."
  type        = string
  sensitive   = true
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token with Zone:DNS:Edit and Workers R2 Storage:Edit."
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (for R2 buckets)."
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for the domain."
  type        = string
}

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
    Hetzner location. fsn1/nbg1 = Germany, hel1 = Finland, ash/hil = US.
    EU is cheapest; US (ash) cuts ~90ms for a US audience at a higher price.
    Open decision — ARCHITECTURE.md §10.1.
  EOT
  type        = string
  default     = "fsn1"

  validation {
    condition     = contains(["fsn1", "nbg1", "hel1", "ash", "hil", "sin"], var.location)
    error_message = "location must be a valid Hetzner Cloud location."
  }
}

variable "server_type" {
  description = <<-EOT
    Hetzner server type. cx52 = 16 vCPU / 32 GB / 320 GB NVMe, ~EUR 32.40/mo,
    which is the budget in ARCHITECTURE.md §4. (The architecture doc said "CX53";
    no such type exists — cx52 is the one with those specs.)
    cx52 is Intel/shared and EU-only; use cpx51 (AMD, 16 vCPU / 32 GB / 360 GB)
    if you move to a US location.
  EOT
  type        = string
  default     = "cx52"
}

variable "ssh_public_key" {
  description = "SSH public key authorised for root on the node."
  type        = string
}

variable "admin_ip_ranges" {
  description = <<-EOT
    CIDRs allowed to reach SSH (22) and the Kubernetes API (6443).
    Do NOT leave this as 0.0.0.0/0 — the API server is the whole cluster.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]

  # validation {
  #   condition     = !contains(var.admin_ip_ranges, "0.0.0.0/0")
  #   error_message = "Refusing to expose SSH and the Kubernetes API to the internet."
  # }
}

variable "k3s_version" {
  description = "Pinned k3s channel or version, e.g. v1.31.5+k3s1. Empty = stable channel."
  type        = string
  default     = ""
}

variable "snapshot_bucket" {
  description = "R2 bucket for versioned DuckDB snapshots and Postgres backups."
  type        = string
  default     = "ohdp-warehouse"
}

variable "enable_dns" {
  description = "Manage DNS records for the platform hostnames in Cloudflare."
  type        = bool
  default     = true
}

variable "subdomains" {
  description = "Hostnames fronted by the ingress. All proxied through Cloudflare."
  type        = list(string)
  default     = ["app", "dagster", "superset", "catalog", "cube"]
}
