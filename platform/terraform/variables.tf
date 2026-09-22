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
  description = "DigitalOcean Spaces bucket for Postgres backups (ARCHITECTURE.md §11)."
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
  # "catalog" is OpenMetadata, not the Iceberg catalog — that is Snowflake
  # (ADR-0019), reached at its own hostname; nothing of ours is served for it.
  default = ["app", "dagster", "catalog", "cube"]
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
# The Iceberg lakehouse: Snowflake is the catalog, S3 is the storage (ADR-0019)
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = <<-EOT
    AWS region for the external volume's bucket. Unrelated to `location`
    (DigitalOcean) — the cluster stays where it is; only the table files live
    here, because Snowflake's external volumes cannot use Spaces.
  EOT
  type        = string
  default     = "us-east-1"
}

variable "aws_access_key_id" {
  description = <<-EOT
    Admin AWS access key Terraform itself uses. Supplied as
    TF_VAR_aws_access_key_id — deliberately not AWS_ACCESS_KEY_ID, which the
    Spaces state backend reads (see versions.tf).
  EOT
  type        = string
  sensitive   = true
}

variable "aws_secret_access_key" {
  description = "Admin AWS secret key Terraform itself uses. TF_VAR_aws_secret_access_key."
  type        = string
  sensitive   = true
}

variable "lakehouse_bucket" {
  description = "S3 bucket behind the external volume, holding every Iceberg table's files."
  type        = string
  default     = "ohdp-lakehouse"
}

variable "lakehouse_sources" {
  description = <<-EOT
    Ingestion sources. Each gets a namespace in the RAW catalog (`RAW.<SOURCE>`)
    and one in CLEAN (`CLEAN.STG_<SOURCE>`), matching
    ohdp_ingestion.naming.schema() and dbt's generate_schema_name.sql.
  EOT
  type        = list(string)
  default     = ["healthdata_gov", "cdc", "cms", "openfda", "openaq"]
}

variable "lakehouse_marts" {
  description = <<-EOT
    Presentation-layer marts. Each gets a `CURATED.<MART>` namespace alongside
    the always-present `CURATED.CORE`.
  EOT
  type        = list(string)
  default = [
    "access",
    "behavioral_health",
    "child_welfare",
    "chronic_disease",
    "education",
    "immunization",
    "infectious_disease",
    "respiratory",
  ]
}

variable "snowflake_storage_aws_iam_user_arn" {
  description = <<-EOT
    STORAGE_AWS_IAM_USER_ARN from `DESC EXTERNAL VOLUME` — the IAM user
    Snowflake assumes the storage role as. Empty on a first apply (the volume
    doesn't exist yet); set it and apply again. See README.md.
  EOT
  type        = string
  default     = ""
}

variable "snowflake_storage_aws_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID from `DESC EXTERNAL VOLUME`. Second apply; see README.md."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Snowflake — the read side of the lakehouse (ADR-0019)
# ---------------------------------------------------------------------------

variable "snowflake_organization_name" {
  description = <<-EOT
    Snowflake organization name. With `snowflake_account_name` it forms the
    account identifier `<org>-<account>` Cube connects to.
    Find both with `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`.
  EOT
  type        = string
}

variable "snowflake_account_name" {
  description = "Snowflake account name (not the account locator)."
  type        = string
}

variable "snowflake_warehouse" {
  description = "Virtual warehouse (compute) for Cube's queries over the lakehouse."
  type        = string
  default     = "OHDP_WH"
}

variable "snowflake_pipeline_role" {
  description = <<-EOT
    Account role everything runs as. It reads and writes the lakehouse, and
    Horizon's OAuth2 scope names it (`session:role:<this>`) — see
    ohdp_shared.settings.horizon_scope, which must agree.
  EOT
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_pipeline_user" {
  description = <<-EOT
    SERVICE user everything authenticates as: the pipeline with a programmatic
    access token over Horizon's Iceberg REST API, Cube with the
    RSA key pair over SQL.
  EOT
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_pat_days_to_expiry" {
  description = <<-EOT
    Lifetime of the pipeline's programmatic access token, in days. Snowflake
    caps this at 365 — unlike the RSA key pair, this credential expires, so put
    a reminder somewhere. Rotation is in README.md.
  EOT
  type        = number
  default     = 365

  validation {
    condition     = var.snowflake_pat_days_to_expiry > 0 && var.snowflake_pat_days_to_expiry <= 365
    error_message = "snowflake_pat_days_to_expiry must be between 1 and 365."
  }
}

variable "snowflake_allowed_ips" {
  description = <<-EOT
    CIDRs allowed to authenticate as the query user. Account-wide network
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
