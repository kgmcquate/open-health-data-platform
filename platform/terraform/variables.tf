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

variable "compute_logs_bucket" {
  description = "DigitalOcean Spaces bucket for Dagster's S3ComputeLogManager (raw stdout/stderr compute logs)."
  type        = string
  default     = "ohdp-compute-logs"
}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for the domain hosting the platform hostnames."
  type        = string
}

variable "dns_base" {
  description = "Base under which per-service A records are created, e.g. open-health-data-platform.org -> app.open-health-data-platform.org."
  type        = string
  default     = "open-health-data-platform.org"
}

variable "dns_hostnames" {
  description = "Service hostnames (left-most label) fronted by the Traefik ingress."
  type        = list(string)
  # "catalog" is OpenMetadata, not Snowflake — the warehouse (ADR-0012) is a
  # Snowflake-hosted endpoint, nothing of ours is served for it.
  default = ["app", "dagster", "catalog", "cube", "streamlit"]
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

# ---------------------------------------------------------------------------
# Snowflake data warehouse (ADR-0012)
# ---------------------------------------------------------------------------

variable "snowflake_organization_name" {
  description = <<-EOT
    Snowflake organization name. With `snowflake_account_name` it forms the
    account identifier `<org>-<account>` dlt and dbt-snowflake connect to.
    Find both with `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`.
  EOT
  type        = string
}

variable "snowflake_account_name" {
  description = "Snowflake account name (not the account locator)."
  type        = string
}

variable "snowflake_warehouse" {
  description = "Virtual warehouse (compute) for the pipeline's dlt loads and dbt-snowflake builds."
  type        = string
  default     = "OHDP_WH"
}

variable "snowflake_sources" {
  description = <<-EOT
    Ingestion sources. Each gets a same-named schema in both the RAW and CLEAN
    databases (ADR-0013), matching ohdp_ingestion.naming.schema() and
    dbt_project.yml's per-folder `+schema`.
  EOT
  type        = list(string)
  default     = ["healthdata_gov"]
}

variable "snowflake_marts" {
  description = <<-EOT
    Presentation-layer marts. Each gets a same-named schema in the CURATED
    database, alongside the always-present "core" schema (ADR-0013). A mart
    can also appear on its own via dbt-snowflake's `CREATE SCHEMA IF NOT
    EXISTS`; list one here to bring it under Terraform.
  EOT
  type        = list(string)
  default     = ["respiratory"]
}

variable "snowflake_data_retention_days" {
  description = "Time Travel window on the database."
  type        = number
  default     = 1
}

variable "snowflake_pipeline_role" {
  description = "Account role the pipeline authenticates as."
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_pipeline_user" {
  description = "SERVICE user the pipeline authenticates as, via its RSA key pair."
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_allowed_ips" {
  description = <<-EOT
    CIDRs allowed to authenticate as the pipeline user. Account-wide network
    policy — see snowflake.tf for why it stays even though key-pair auth
    doesn't strictly require one.

    The default allows everything. Narrowing it to the DOKS egress IPs only
    works once those IPs are stable — node recycles and pool resizes change
    them, and a stale entry here locks out the whole account.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.snowflake_allowed_ips) > 0
    error_message = "snowflake_allowed_ips must list at least one CIDR — an empty policy blocks the pipeline."
  }
}
