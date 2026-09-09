variable "kamatera_api_client_id" {
  description = "Kamatera API client ID (console → API keys)."
  type        = string
  sensitive   = true
}

variable "kamatera_api_secret" {
  description = "Kamatera API secret paired with the client ID."
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

variable "datacenter_country" {
  description = "Kamatera datacenter country, matched by the kamatera_datacenter data source."
  type        = string
  default     = "United States"
}

variable "datacenter_name" {
  description = <<-EOT
    Kamatera datacenter name within the country. "New York" for the US audience
    (ARCHITECTURE.md §10.1). Kamatera also exposes these as codes like US-NY2 —
    if the name lookup is ambiguous, check `terraform console` against the
    data source.
  EOT
  type        = string
  default     = "New York"
}

variable "image_code" {
  description = "Kamatera image code for the OS, e.g. '24.04 64bit' for Ubuntu."
  type        = string
  default     = "24.04 64bit"
}

variable "cpu_type" {
  description = <<-EOT
    Kamatera CPU class: A = availability (oversubscribed), B = general purpose,
    D = dedicated, T = burstable. B is the default; move to D if Postgres or
    OpenSearch latency suffers.
  EOT
  type        = string
  default     = "B"

  validation {
    condition     = contains(["A", "B", "D", "T"], var.cpu_type)
    error_message = "cpu_type must be one of A, B, D, T."
  }
}

variable "cpu_cores" {
  description = "vCPU cores. 8 covers the §4 workload (memory-bound, not CPU-bound)."
  type        = number
  default     = 8
}

variable "ram_mb" {
  description = <<-EOT
    RAM in MB. 32768 is the ARCHITECTURE.md §4 budget — steady ~13 GB, burst ~15
    GB, with OpenSearch (3 GB) and OpenMetadata (2 GB) as the big consumers.
    Below 32 GB you must cut one of them (ADR-0005).
  EOT
  type        = number
  default     = 32768
}

variable "disk_size_gb" {
  description = "Primary disk in GB. Holds container images, PVCs (Postgres, OpenSearch) and the NVMe snapshot cache."
  type        = number
  default     = 300
}

variable "billing_cycle" {
  description = "Kamatera billing cycle: 'hourly' or 'monthly'."
  type        = string
  default     = "monthly"

  validation {
    condition     = contains(["hourly", "monthly"], var.billing_cycle)
    error_message = "billing_cycle must be 'hourly' or 'monthly'."
  }
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
