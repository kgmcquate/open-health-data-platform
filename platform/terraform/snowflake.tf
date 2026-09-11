# Snowflake data warehouse for the medallion lake (ADR-0013, which replaces
# ADR-0012's single database + layer-prefixed schemas with one database per
# medallion layer; ADR-0012 itself supersedes ADR-0010's Iceberg medallion
# lakehouse and ADR-0011's Horizon Catalog in full). Plain Snowflake tables:
# dlt and dbt-snowflake talk to these databases directly over SQL, no REST
# catalog, no pyiceberg, no vended credentials.
#
#   database -> one per medallion layer: RAW, CLEAN, CURATED
#   schema   -> one per source (in RAW/CLEAN) or mart (in CURATED; "core"
#               always exists there too)
#   table    -> a plain Snowflake table in that schema

locals {
  # <organization>-<account>, the account identifier dlt/dbt-snowflake connect
  # to as `host`/`account`.
  snowflake_account_identifier = "${var.snowflake_organization_name}-${var.snowflake_account_name}"

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

  # One database per medallion layer (ADR-0013). Names match
  # ohdp_ingestion.naming.database() exactly — that module, not this file, is
  # what the pipeline reads at runtime, so a rename here must be mirrored
  # there (and in dbt_project.yml's `+database` for clean/core/marts).
  snowflake_layer_databases = {
    raw     = "RAW"
    clean   = "CLEAN"
    curated = "CURATED"
  }

  # Schemas to create, keyed uniquely for a stable for_each. Every source gets
  # a same-named schema in both RAW and CLEAN; CURATED always gets "core" plus
  # one schema per mart. Matches ohdp_ingestion.naming.schema() and
  # dbt_project.yml's per-folder `+schema`.
  snowflake_schemas = merge(
    # { for s in var.snowflake_sources : upper("raw.${s}") => { layer = "raw", schema = s } },
    # { for s in var.snowflake_sources : upper("clean.${s}") => { layer = "clean", schema = s } },
    # { upper("curated.core") = { layer = "curated", schema = "core" } },
    # { for m in var.snowflake_marts : upper("curated.${m}") => { layer = "curated", schema = m } },
  )
}

# Snowflake SERVICE users don't accept password auth (and PATs, which do, are
# only usable through Horizon's REST/OAuth2 path — not a plain SQL/JDBC/Python
# connector login, which is what dlt and dbt-snowflake both need). Key-pair is
# the supported mechanism: generate it here rather than requiring a human to
# run openssl and paste a public key into terraform.tfvars.
resource "tls_private_key" "pipeline" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# ---------------------------------------------------------------------------
# The warehouse: one database per medallion layer, one schema per source/mart
# within it. Unquoted lowercase names here fold to the same identifier in
# ad-hoc SQL either way — Snowflake's ordinary case-insensitive resolution
# applies, unlike the old Iceberg REST path where the catalog passed names
# through verbatim.
# ---------------------------------------------------------------------------
resource "snowflake_database" "layer" {
  for_each = local.snowflake_layer_databases

  name    = each.value
  comment = "OHDP ${each.key} layer database — plain Snowflake tables (ADR-0013)."

  data_retention_time_in_days = var.snowflake_data_retention_days
}

# resource "snowflake_schema" "namespace" {
#   for_each = local.snowflake_schemas

#   database = snowflake_database.layer[each.value.layer].name
#   name     = each.value.schema
#   comment  = "Schema ${each.value.schema} in the ${each.value.layer} layer database (ADR-0013)."

#   # dbt's CREATE SCHEMA IF NOT EXISTS can open a new mart schema ahead of a
#   # Terraform run; adding it to snowflake_marts later adopts it rather than
#   # fighting over it.
#   lifecycle {
#     ignore_changes = [comment]
#   }
# }

# ---------------------------------------------------------------------------
# Compute for dlt loads and dbt-snowflake builds. XS, suspended after a
# minute, and created suspended: idle costs nothing.
# ---------------------------------------------------------------------------
resource "snowflake_warehouse" "ohdp" {
  name                = var.snowflake_warehouse
  warehouse_size      = "XSMALL"
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  comment             = "Compute for the Dagster pipeline's dlt loads and dbt-snowflake builds."
}

# ---------------------------------------------------------------------------
# The pipeline identity: one role, one SERVICE user, one RSA key pair.
# ---------------------------------------------------------------------------
resource "snowflake_account_role" "pipeline" {
  name    = var.snowflake_pipeline_role
  comment = "Read/write on the OHDP warehouse databases for the Dagster pipeline (dlt raw loads, dbt-snowflake builds)."
}

# USAGE lets the role see each database; CREATE SCHEMA lets the dbt plugin's
# create_namespace_if_not_exists open a new mart schema without a Terraform run.
resource "snowflake_grant_privileges_to_account_role" "database" {
  for_each = snowflake_database.layer

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE SCHEMA"]

  on_account_object {
    object_type = "DATABASE"
    object_name = each.value.name
  }
}

# Existing and future schemas. CREATE TABLE is what lets dbt-snowflake and dlt
# create tables; USAGE is what lets them resolve the schema at all.
resource "snowflake_grant_privileges_to_account_role" "schemas_existing" {
  for_each = snowflake_database.layer

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE TABLE"]

  on_schema {
    all_schemas_in_database = each.value.fully_qualified_name
  }

  depends_on = [snowflake_schema.namespace]
}

resource "snowflake_grant_privileges_to_account_role" "schemas_future" {
  for_each = snowflake_database.layer

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE TABLE"]

  on_schema {
    future_schemas_in_database = each.value.fully_qualified_name
  }
}

# Tables the role did not create itself. A table created by dlt/dbt-snowflake
# is owned by this role, which already has everything on it; these grants
# cover tables made in Snowsight or by a future second identity. The full DML
# set is required — dbt's `merge` incremental strategy and dlt's write
# dispositions between them exercise SELECT, INSERT, UPDATE, DELETE and
# TRUNCATE.
resource "snowflake_grant_privileges_to_account_role" "tables_existing" {
  for_each = snowflake_database.layer

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]

  on_schema_object {
    all {
      object_type_plural = "TABLES"
      in_database        = each.value.fully_qualified_name
    }
  }

  depends_on = [snowflake_schema.namespace]
}

resource "snowflake_grant_privileges_to_account_role" "tables_future" {
  for_each = snowflake_database.layer

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]

  on_schema_object {
    future {
      object_type_plural = "TABLES"
      in_database        = each.value.fully_qualified_name
    }
  }
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
# Network policy, account-wide. Not required for key-pair auth specifically,
# but left in place: it was already proven working, and `snowflake_allowed_ips`
# defaulting to everything means keeping it costs nothing.
#
# `snowflake_allowed_ips` gates every session in the account, human logins
# included, not just this service user. Narrow it the moment you know the
# egress IP the DOKS nodes present — but know that DigitalOcean node public
# IPs change when a node is recycled or the pool is resized, and a stale entry
# locks out everyone, not just the pipeline. A NAT gateway with a stable IP is
# the prerequisite for making this real.
# ---------------------------------------------------------------------------
resource "snowflake_network_policy" "pipeline" {
  name            = "${var.snowflake_pipeline_role}_NETWORK_POLICY"
  allowed_ip_list = var.snowflake_allowed_ips
  comment         = "Account-wide network policy."
}

resource "snowflake_network_policy_attachment" "pipeline" {
  network_policy_name = snowflake_network_policy.pipeline.name
  set_for_account     = true
}

resource "snowflake_service_user" "pipeline" {
  name           = var.snowflake_pipeline_user
  comment        = "Dagster pipeline. Authenticates to Snowflake with a key pair (dlt loads, dbt-snowflake builds)."
  default_role   = snowflake_account_role.pipeline.name
  rsa_public_key = local.snowflake_pipeline_rsa_public_key

  # dlt and dbt-snowflake both connect with exactly one role; no secondary
  # roles should come along for the ride.
  default_secondary_roles_option = "NONE"
}

resource "snowflake_grant_account_role" "pipeline" {
  role_name = snowflake_account_role.pipeline.name
  user_name = snowflake_service_user.pipeline.name
}
