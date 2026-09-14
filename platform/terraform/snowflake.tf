# Snowflake *is* the Iceberg catalog (ADR-0019, which supersedes ADR-0012's
# native tables and ADR-0014's Snowflake-only compilation, and keeps ADR-0013's
# per-layer databases). The medallion layers are Snowflake-managed Iceberg
# tables; Snowflake's Horizon catalog serves their databases over the open
# Iceberg REST protocol, so dlt and dbt-duckdb write the same tables Cube and
# Streamlit read over SQL. The files sit in an external volume on S3 (aws.tf).
#
# This is the shape ADR-0011 wanted and ADR-0012 abandoned. What changed is
# that writing to Snowflake-managed Iceberg tables from an external engine went
# GA in May 2026 — before that, external engines could only read.
#
#   external volume -> where the table files live (S3, aws.tf)
#   database        -> one per medallion layer (RAW/CLEAN/CURATED, ADR-0013),
#                      each a catalog in its own right, with CATALOG='SNOWFLAKE'
#   schema          -> a namespace in it: RAW.<SOURCE>, CLEAN.STG_<SOURCE>,
#                      CURATED.CORE, CURATED.<MART>
#   PAT             -> the pipeline's credential for the REST endpoint
#
# The layer databases are unchanged from ADR-0013 — a Snowflake database is an
# Iceberg catalog and its schemas are that catalog's namespaces, so nothing had
# to be flattened together to fit the catalog's shape.
#
# The external volume, the database defaults and the grants go through
# `snowflake_execute`: the provider has no resource for a catalog-enabled
# database and its `snowflake_external_volume` is a preview resource. Changing
# `execute` or `revert` forces a new resource, so each object carries its whole
# definition and any rename re-creates it — hence CREATE OR REPLACE rather than
# CREATE IF NOT EXISTS.

locals {
  # <organization>-<account>, the account identifier. Also what the Horizon
  # REST endpoint is addressed by:
  #   https://<identifier>.snowflakecomputing.com/polaris/api/catalog
  snowflake_account_identifier = "${var.snowflake_organization_name}-${var.snowflake_account_name}"
  horizon_catalog_uri          = "https://${local.snowflake_account_identifier}.snowflakecomputing.com/polaris/api/catalog"

  # snowflake_service_user.rsa_public_key must be the base64 body on one line,
  # no PEM header/footer — strip both from tls_private_key's PEM output.
  snowflake_pipeline_rsa_public_key = replace(
    replace(
      replace(tls_private_key.pipeline.public_key_pem, "-----BEGIN PUBLIC KEY-----", ""),
      "-----END PUBLIC KEY-----", ""
    ),
    "\n", ""
  )

  # PKCS#8 PEM, what Snowflake's connectors expect for key-pair auth. Shared by
  # the snowflake_private_key output and the Spaces object in r2.tf so there is
  # exactly one place this gets assembled.
  snowflake_private_key = tls_private_key.pipeline.private_key_pem_pkcs8

  snowflake_external_volume = upper("${var.name}_lakehouse_vol")

  # One database per medallion layer (ADR-0013), each of which is also an
  # Iceberg catalog and a DuckDB `ATTACH` alias. Names match
  # ohdp_ingestion.naming.database() exactly — that module, not this file, is
  # what the pipeline reads at runtime, so a rename here must be mirrored there
  # (and in dbt_project.yml's `+database`).
  snowflake_layer_databases = {
    raw     = "RAW"
    clean   = "CLEAN"
    curated = "CURATED"
  }

  # Namespaces to create, keyed uniquely for a stable for_each. Every source
  # gets a schema in RAW (named for the source) and one in CLEAN (the
  # `STG_<SOURCE>` staging convention the model names already carry); CURATED
  # always gets CORE plus one schema per mart. Matches
  # ohdp_ingestion.naming.schema() and dbt's generate_schema_name.sql.
  #
  # Upper case because Snowflake resolves unquoted identifiers that way and
  # Horizon requires external engines to address them in capitals.
  #
  # dlt and dbt-duckdb both open a missing namespace themselves, so this is
  # about making the structure legible and grantable, not about unblocking the
  # pipeline.
  snowflake_namespaces = merge(
    { for s in var.lakehouse_sources : upper("raw.${s}") => {
      database = "RAW", schema = upper(s)
    } },
    { for s in var.lakehouse_sources : upper("clean.stg_${s}") => {
      database = "CLEAN", schema = upper("stg_${s}")
    } },
    { "CURATED.CORE" = { database = "CURATED", schema = "CORE" } },
    { for m in var.lakehouse_marts : upper("curated.${m}") => {
      database = "CURATED", schema = upper(m)
    } },
  )
}

# Snowflake SERVICE users don't accept password auth. Two credentials hang off
# this one user, because the two protocols authenticate differently:
#
#   RSA key pair -> the SQL connector (Cube, Streamlit)
#   PAT          -> the Iceberg REST catalog (dlt, dbt via DuckDB)
#
# ADR-0012 removed the PAT on the grounds that nothing did an OAuth2 exchange
# any more. Something does again, and it is the whole pipeline.
resource "tls_private_key" "pipeline" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# ---------------------------------------------------------------------------
# Compute for Cube's and Streamlit's queries. XS, suspended after a minute, and
# created suspended: idle costs nothing. dbt builds no longer run here — that
# is DuckDB in the pipeline pod — so this is sized for serving only.
# ---------------------------------------------------------------------------
resource "snowflake_warehouse" "ohdp" {
  name                = var.snowflake_warehouse
  warehouse_size      = "XSMALL"
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  comment             = "Compute for Cube's metric queries and Streamlit's ad-hoc ones over the lakehouse."
}

# ---------------------------------------------------------------------------
# The pipeline identity: one role, one SERVICE user, one key pair, one PAT.
# ---------------------------------------------------------------------------
resource "snowflake_account_role" "pipeline" {
  name    = var.snowflake_pipeline_role
  comment = "Reads and writes the RAW/CLEAN/CURATED Iceberg catalogs; the role Horizon's OAuth2 scope names."
}

resource "snowflake_grant_privileges_to_account_role" "warehouse" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.ohdp.name
  }
}

# ---------------------------------------------------------------------------
# Network policy, account-wide. Load-bearing again: Snowflake requires a
# network policy on any user that authenticates with a programmatic access
# token, so the PAT below does not work without it. ADR-0012 kept this around
# only because it was already proven; it has a job once more.
#
# `snowflake_allowed_ips` gates every session in the account, human logins
# included. Narrow it the moment you know the egress IP the DOKS nodes present
# — but know that DigitalOcean node public IPs change when a node is recycled
# or the pool is resized, and a stale entry locks out everyone.
# ---------------------------------------------------------------------------
resource "snowflake_network_policy" "pipeline" {
  name            = "${var.snowflake_pipeline_role}_NETWORK_POLICY"
  allowed_ip_list = var.snowflake_allowed_ips
  comment         = "Account-wide network policy. Required for programmatic access tokens."
}

resource "snowflake_network_policy_attachment" "pipeline" {
  network_policy_name = snowflake_network_policy.pipeline.name
  set_for_account     = true
}

resource "snowflake_service_user" "pipeline" {
  name           = var.snowflake_pipeline_user
  comment        = "The pipeline (PAT, over Horizon's Iceberg REST API) and Cube/Streamlit (key pair, over SQL)."
  default_role   = snowflake_account_role.pipeline.name
  rsa_public_key = local.snowflake_pipeline_rsa_public_key

  # Both protocols connect with exactly one role — and Horizon's OAuth2 scope
  # names it explicitly — so no secondary roles should come along for the ride.
  default_secondary_roles_option = "NONE"
}

resource "snowflake_grant_account_role" "pipeline" {
  role_name = snowflake_account_role.pipeline.name
  user_name = snowflake_service_user.pipeline.name
}

# The catalog credential. `days_to_expiry` is capped at 365 by Snowflake, so
# unlike the RSA key pair this one has a deadline — see the README for the
# rotation, which is a `terraform apply -replace` plus one secret push.
resource "snowflake_user_programmatic_access_token" "pipeline" {
  user             = snowflake_service_user.pipeline.name
  name             = upper("${var.name}_horizon_pat")
  role_restriction = snowflake_account_role.pipeline.name
  days_to_expiry   = var.snowflake_pat_days_to_expiry
  comment          = "Pipeline credential for the Horizon Iceberg REST catalog (dlt + dbt/DuckDB)."

  depends_on = [
    snowflake_grant_account_role.pipeline,
    snowflake_network_policy_attachment.pipeline,
  ]
}

# ---------------------------------------------------------------------------
# The lakehouse itself.
# ---------------------------------------------------------------------------

# Where the table files live. ALLOW_WRITES is TRUE, unlike a read-only external
# catalog setup: Snowflake manages these tables, so it writes the data, the
# metadata and the compaction output.
resource "snowflake_execute" "external_volume" {
  execute = <<-SQL
    CREATE OR REPLACE EXTERNAL VOLUME ${local.snowflake_external_volume}
      STORAGE_LOCATIONS = (
        (
          NAME = 'lakehouse-s3'
          STORAGE_PROVIDER = 'S3'
          STORAGE_BASE_URL = 's3://${aws_s3_bucket.lakehouse.bucket}/'
          STORAGE_AWS_ROLE_ARN = '${aws_iam_role.snowflake_storage.arn}'
        )
      )
      ALLOW_WRITES = TRUE
      COMMENT = 'Storage for the OHDP Snowflake-managed Iceberg tables (ADR-0019).';
  SQL

  revert = "DROP EXTERNAL VOLUME IF EXISTS ${local.snowflake_external_volume};"
}

# The catalogs. `CATALOG = 'SNOWFLAKE'` and `EXTERNAL_VOLUME` are set as
# *database defaults*, so every table created in one — including the ones dlt
# and dbt create through the REST API, which pass neither — is a
# Snowflake-managed Iceberg table in our bucket.
resource "snowflake_execute" "database" {
  for_each = local.snowflake_layer_databases

  execute = <<-SQL
    CREATE OR REPLACE DATABASE ${each.value}
      EXTERNAL_VOLUME = '${local.snowflake_external_volume}'
      CATALOG = 'SNOWFLAKE'
      COMMENT = 'OHDP ${each.key} layer — an Iceberg catalog, written over Horizon (ADR-0019).';
  SQL

  revert = "DROP DATABASE IF EXISTS ${each.value};"

  depends_on = [snowflake_execute.external_volume]
}

resource "snowflake_execute" "namespace" {
  for_each = local.snowflake_namespaces

  execute = <<-SQL
    CREATE SCHEMA IF NOT EXISTS ${each.value.database}.${each.value.schema}
      COMMENT = 'OHDP lakehouse namespace ${each.value.database}.${each.value.schema} (ADR-0013).';
  SQL

  revert = "DROP SCHEMA IF EXISTS ${each.value.database}.${each.value.schema};"

  depends_on = [snowflake_execute.database]
}

# Read *and* write, per layer database: the pipeline creates and rewrites
# tables through the REST catalog, and CREATE SCHEMA is what lets a new mart
# appear without a Terraform run. Cube and Streamlit share the role but only
# ever SELECT.
#
# `snowflake_execute` rather than the provider's grant resources: those read
# back through SHOW GRANTS against a database they expect to manage, and this
# one's schemas come and go as the pipeline creates them.
resource "snowflake_execute" "lakehouse_grants" {
  for_each = local.snowflake_layer_databases

  execute = <<-SQL
    GRANT USAGE, CREATE SCHEMA ON DATABASE ${each.value}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT USAGE, CREATE ICEBERG TABLE ON ALL SCHEMAS IN DATABASE ${each.value}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT USAGE, CREATE ICEBERG TABLE ON FUTURE SCHEMAS IN DATABASE ${each.value}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL ICEBERG TABLES IN DATABASE ${each.value}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT SELECT, INSERT, UPDATE, DELETE ON FUTURE ICEBERG TABLES IN DATABASE ${each.value}
      TO ROLE ${snowflake_account_role.pipeline.name};
  SQL

  revert = <<-SQL
    REVOKE USAGE, CREATE SCHEMA ON DATABASE ${each.value}
      FROM ROLE ${snowflake_account_role.pipeline.name};
  SQL

  depends_on = [snowflake_execute.namespace]
}

# A table's files are resolved through the external volume, so the role needs
# USAGE on it too — the database grant alone is not enough.
resource "snowflake_execute" "grant_external_volume" {
  execute = <<-SQL
    GRANT USAGE ON EXTERNAL VOLUME ${local.snowflake_external_volume}
      TO ROLE ${snowflake_account_role.pipeline.name};
  SQL

  revert = <<-SQL
    REVOKE USAGE ON EXTERNAL VOLUME ${local.snowflake_external_volume}
      FROM ROLE ${snowflake_account_role.pipeline.name};
  SQL

  depends_on = [snowflake_execute.external_volume]
}
