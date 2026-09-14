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
  description = <<-EOT
    AWS S3 bucket for Dagster's S3ComputeLogManager (raw stdout/stderr compute
    logs). On AWS rather than Spaces because that log manager reads its
    credentials off the process environment, which the pipeline pod now needs
    for the lakehouse (ADR-0019).
  EOT
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
  # "catalog" is OpenMetadata, not the Iceberg catalog — that is AWS Glue
  # (ADR-0019), an AWS-hosted endpoint; nothing of ours is served for it.
  # "chat" is Open WebUI (ADR-0017), a second chat surface alongside "app"
  # (hub-api's own UI); "app" is not being retired by it.
  default = ["app", "chat", "dagster", "catalog", "cube", "streamlit"]
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
# The Iceberg lakehouse: AWS S3 + the Glue catalog (ADR-0019)
# ---------------------------------------------------------------------------

variable "aws_region" {
  description = <<-EOT
    AWS region for the lakehouse bucket and the Glue catalog. Unrelated to
    `location` (DigitalOcean) — the cluster stays where it is; only the tables
    live here, because Snowflake cannot read Iceberg from Spaces.
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
  description = "S3 bucket holding every Iceberg table's data and metadata files."
  type        = string
  default     = "ohdp-lakehouse"
}

variable "lakehouse_catalog" {
  description = <<-EOT
    Name both engines know the catalog by: DuckDB's `ATTACH ... AS <name>`
    alias (data/dbt/profiles.yml) and the Snowflake catalog-linked database.
    Must equal ohdp_ingestion.naming.CATALOG — that module is what the pipeline
    reads at runtime.
  EOT
  type        = string
  default     = "lakehouse"
}

variable "lakehouse_sources" {
  description = <<-EOT
    Ingestion sources. Each gets a `raw_<source>` and a `clean_<source>`
    namespace in the Glue catalog (ADR-0019), matching
    ohdp_ingestion.naming.schema() and dbt's generate_schema_name.sql.
  EOT
  type        = list(string)
  default     = ["healthdata_gov", "cdc"]
}

variable "lakehouse_marts" {
  description = <<-EOT
    Presentation-layer marts. Each gets a `mart_<name>` namespace alongside the
    always-present `core` one. dlt and dbt-duckdb can also open a namespace on
    their own; list one here to bring it under Terraform.
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

variable "snowflake_glue_aws_iam_user_arn" {
  description = <<-EOT
    API_AWS_IAM_USER_ARN from `DESC CATALOG INTEGRATION` — the IAM user
    Snowflake assumes the Glue role as. Second apply; see README.md.
  EOT
  type        = string
  default     = ""
}

variable "snowflake_glue_aws_external_id" {
  description = "API_AWS_EXTERNAL_ID from `DESC CATALOG INTEGRATION`. Second apply; see README.md."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Snowflake — the read side of the lakehouse (ADR-0019)
# ---------------------------------------------------------------------------

variable "snowflake_organization_name" {
  description = <<-EOT
    Snowflake organization name. With `snowflake_account_name` it forms the
    account identifier `<org>-<account>` Cube and Streamlit connect to.
    Find both with `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME()`.
  EOT
  type        = string
}

variable "snowflake_account_name" {
  description = "Snowflake account name (not the account locator)."
  type        = string
}

variable "snowflake_warehouse" {
  description = "Virtual warehouse (compute) for Cube's and Streamlit's queries over the lakehouse."
  type        = string
  default     = "OHDP_WH"
}

variable "snowflake_pipeline_role" {
  description = <<-EOT
    Account role Cube and Streamlit authenticate as. Read-only over the
    catalog-linked database (ADR-0019). Name kept from when it was the
    pipeline's write role so the existing repo/Kubernetes secrets and Helm
    values don't all have to rotate at once.
  EOT
  type        = string
  default     = "OHDP_PIPELINE"
}

variable "snowflake_pipeline_user" {
  description = "SERVICE user Cube and Streamlit authenticate as, via its RSA key pair."
  type        = string
  default     = "OHDP_PIPELINE"
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
