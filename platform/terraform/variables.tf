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
  # `polaris` is gone with the Polaris console (ADR-0011). The Horizon Catalog
  # is a Snowflake-hosted endpoint — nothing of ours is served for it.
  default = ["app", "dagster", "catalog", "cube", "superset"]
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
# Snowflake Horizon Catalog (ADR-0011)
# ---------------------------------------------------------------------------

variable "snowflake_organization_name" {
  description = <<-EOT
    Snowflake organization name. With `snowflake_account_name` it forms the
    account identifier `<org>-<account>` used in the Horizon Catalog URL.
    Find both with `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`.
  EOT
  type        = string
}

variable "snowflake_account_name" {
  description = "Snowflake account name (not the account locator)."
  type        = string
}

variable "snowflake_database" {
  description = "Snowflake database backing the lake. This is the Iceberg REST `warehouse` the pipeline attaches to."
  type        = string
  default     = "OHDP"
}

variable "snowflake_warehouse" {
  description = "Virtual warehouse for ad-hoc SQL. The pipeline does not use it — DuckDB is the compute."
  type        = string
  default     = "OHDP_WH"
}

variable "snowflake_namespaces" {
  description = <<-EOT
    Iceberg namespaces to create as schemas, lowercase to match what the
    pipeline sends over REST (ohdp_ingestion.iceberg.namespace). Marts can also
    appear on their own via the dbt plugin's create_namespace_if_not_exists;
    list one here to bring it under Terraform.
  EOT
  type        = list(string)
  default     = ["raw_healthdata_gov", "clean_healthdata_gov", "core"]
}

variable "snowflake_data_retention_days" {
  description = "Time Travel window on the database. Iceberg snapshot history is separate and unaffected."
  type        = number
  default     = 1
}

variable "snowflake_pipeline_role" {
  description = "Account role the pipeline authenticates as. Becomes the REST `scope` — session:role:<this>."
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_pipeline_user" {
  description = "SERVICE user the pipeline's PAT belongs to. Becomes the client_id half of OHDP_ICEBERG_CREDENTIAL."
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_allowed_ips" {
  description = <<-EOT
    CIDRs allowed to authenticate as the pipeline user. Snowflake requires a
    network policy on a SERVICE user before it will issue or accept a PAT at
    all, so this exists whether or not you want to restrict anything.

    The default allows everything. Narrowing it to the DOKS egress IPs only
    works once those IPs are stable — node recycles and pool resizes change
    them, and a stale entry here fails every pipeline run with a 401.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.snowflake_allowed_ips) > 0
    error_message = "snowflake_allowed_ips must list at least one CIDR — an empty policy blocks the pipeline."
  }
}

variable "snowflake_pat_days_to_expiry" {
  description = "Lifetime of the pipeline's programmatic access token. Snowflake caps this at 365."
  type        = number
  default     = 365

  validation {
    condition     = var.snowflake_pat_days_to_expiry > 0 && var.snowflake_pat_days_to_expiry <= 365
    error_message = "snowflake_pat_days_to_expiry must be between 1 and 365 (Snowflake's ceiling)."
  }
}

variable "snowflake_pat_keeper" {
  description = <<-EOT
    Change this to any new non-empty value to rotate the pipeline's PAT on the
    next apply. Terraform rotates only when it moves from one non-empty value to
    a different one; adding or removing the field does nothing. Re-run the
    platform deploy afterwards so the cluster secret picks up the new token.
  EOT
  type        = string
  default     = "v1"
}
