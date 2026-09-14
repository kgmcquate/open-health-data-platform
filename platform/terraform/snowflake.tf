# Snowflake *is* the Iceberg catalog (ADR-0019, which supersedes ADR-0012's
# native tables and ADR-0014's Snowflake-only compilation, and keeps ADR-0013's
# per-layer databases). Horizon serves the layer databases over the open
# Iceberg REST protocol, so dlt and dbt-duckdb write the same tables Cube and
# Streamlit read over SQL.
#
# **Snowflake holds the metadata, not the bytes.** Every table's files live in
# our own S3 bucket, reached through the external volume below — Snowflake's
# own managed storage is never used. `CATALOG = 'SNOWFLAKE'` names who owns the
# *catalog*, which is a separate question from where the data sits; the
# `EXTERNAL_VOLUME` on each database is what answers the storage one, and it is
# the single load-bearing setting for it (see the database resource).
#
# This is the shape ADR-0011 wanted and ADR-0012 abandoned — with the storage
# question answered the other way round, since ADR-0011 put the files in
# Snowflake's own storage and this does not. What also changed is that writing
# to Snowflake-catalogued Iceberg tables from an external engine went GA in
# May 2026; before that, external engines could only read.
#
#   external volume -> where the table files live (S3, aws.tf)
#   database        -> one per medallion layer (RAW/CLEAN/CURATED, ADR-0013),
#                      each a catalog in its own right, with CATALOG='SNOWFLAKE'
#   PAT             -> the pipeline's credential for the REST endpoint
#
# Namespaces (RAW.<SOURCE>, CLEAN.STG_<SOURCE>, CURATED.CORE, CURATED.<MART>)
# ARE here (ADR-0021, reversing ADR-0020): Horizon's Iceberg REST catalog does
# not implement namespace creation for external engines — `create_namespace`
# 404s — so a schema has to exist before dlt or dbt-duckdb can write into it
# over REST. Terraform is what creates it.
#
# The layer databases are unchanged from ADR-0013 — a Snowflake database is an
# Iceberg catalog and its schemas are that catalog's namespaces, so nothing had
# to be flattened together to fit the catalog's shape.
#
# All of it is typed provider resources, no raw DDL: `snowflake_database`
# carries CATALOG and EXTERNAL_VOLUME as first-class arguments, and
# `snowflake_external_volume` is a preview resource opted into by name in
# versions.tf's `preview_features_enabled`.

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

  # Every namespace in the lakehouse (ADR-0021): RAW.<SOURCE>, CLEAN.STG_<SOURCE>
  # (dbt-labs staging convention), CURATED.CORE (always present) and
  # CURATED.<MART>. Names match ohdp_ingestion.naming.schema() and dbt's
  # generate_schema_name.sql exactly — a rename here must be mirrored there.
  #
  # Upper case because Snowflake resolves unquoted identifiers that way and
  # Horizon requires external engines to address them in capitals.
  #
  # `layer` is the snowflake_layer_databases key, not the database name, so the
  # schema resource can reference snowflake_database.layer[...] and inherit the
  # dependency edge rather than declaring one.
  snowflake_namespaces = merge(
    { for s in var.lakehouse_sources : upper("raw.${s}") => {
      layer = "raw", schema = upper(s)
    } },
    { for s in var.lakehouse_sources : upper("clean.stg_${s}") => {
      layer = "clean", schema = upper("stg_${s}")
    } },
    { "CURATED.CORE" = { layer = "curated", schema = "CORE" } },
    { for m in var.lakehouse_marts : upper("curated.${m}") => {
      layer = "curated", schema = upper(m)
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

# Where the table files live: the S3 bucket in aws.tf, ours. ALLOW_WRITES is
# TRUE because Snowflake does write into it — table metadata, and the
# compaction and snapshot-expiry output for the tables it catalogues — but it
# writes to *our* bucket under *our* IAM role, and nothing lands in
# Snowflake-owned storage.
#
# Still a preview resource, hence the opt-in in versions.tf. `allow_writes` is
# a string, not a bool: the provider uses an unset-vs-"false" distinction that
# a bool cannot carry.
resource "snowflake_external_volume" "lakehouse" {
  name         = local.snowflake_external_volume
  allow_writes = "true"
  comment      = "OHDP lakehouse storage: our S3 bucket, not Snowflake storage (ADR-0019)."

  storage_location {
    storage_location_name = "lakehouse-s3"
    storage_provider      = "S3"
    storage_base_url      = "s3://${aws_s3_bucket.lakehouse.bucket}/"
    storage_aws_role_arn  = aws_iam_role.snowflake_storage.arn
  }
}

# The catalogs. `catalog` and `external_volume` are *database defaults*, and
# Iceberg tables inherit both through table -> schema -> database, so every
# table created in one — including the ones dlt and dbt create through the REST
# API, which pass neither — is an Iceberg table catalogued by Snowflake with
# its files in our S3 bucket.
#
# external_volume is the setting that keeps the data out of Snowflake's own
# storage. Dropping it here would not fail anything loudly; it would silently
# start putting new tables somewhere we do not control. Do not remove it, and
# do not create an Iceberg table in these databases with an explicit
# EXTERNAL_VOLUME pointing elsewhere.
#
# is_transient is set explicitly because the provider recreates — i.e. drops —
# a database whose boolean it cannot read back as set. Leave it here.
resource "snowflake_database" "layer" {
  for_each = local.snowflake_layer_databases

  name            = each.value
  catalog         = "SNOWFLAKE"
  external_volume = snowflake_external_volume.lakehouse.name
  is_transient    = false
  comment         = "OHDP ${each.key} layer — an Iceberg catalog, written over Horizon (ADR-0019)."
}

# The catalogs' namespaces (ADR-0021, reversing ADR-0020's "pipeline creates
# its own namespaces"): Horizon's Iceberg REST catalog turns out not to
# implement namespace creation for external engines at all — `create_namespace`
# 404s whether it's pyiceberg (dlt) or DuckDB's REST-catalog client (dbt)
# asking — so a schema has to exist before either can write into it. Terraform
# is what creates it, from `var.lakehouse_sources` and `var.lakehouse_marts`.
#
# Same reason as the database for pinning with_managed_access and is_transient
# explicitly — an unset boolean reads back as a diff, and the diff is a drop.
resource "snowflake_schema" "namespace" {
  for_each = local.snowflake_namespaces

  database            = snowflake_database.layer[each.value.layer].name
  name                = each.value.schema
  is_transient        = "false"
  with_managed_access = "false"
  comment             = "OHDP lakehouse namespace ${local.snowflake_layer_databases[each.value.layer]}.${each.value.schema} (ADR-0021)."
}

# Read *and* write, per layer database: the pipeline creates and rewrites
# tables through the REST catalog. Cube and Streamlit share the role but only
# ever SELECT.
#
# Five grants per database, because ALL and FUTURE are different statements in
# Snowflake and so different resources here. ALL covers what exists at apply
# time — the schemas above, which Terraform's own role owns, not the
# pipeline's — FUTURE covers any table the pipeline creates afterwards inside
# them. CREATE SCHEMA stays on the database grant too: adding a mart or source
# still means a Terraform apply first, but a schema the pipeline opens itself
# (nothing should, post-ADR-0021, but nothing forbids it either) still works.
resource "snowflake_grant_privileges_to_account_role" "lakehouse_database" {
  for_each = local.snowflake_layer_databases

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE SCHEMA"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.layer[each.key].name
  }
}

resource "snowflake_grant_privileges_to_account_role" "lakehouse_all_schemas" {
  for_each = local.snowflake_layer_databases

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE ICEBERG TABLE"]

  on_schema {
    all_schemas_in_database = snowflake_database.layer[each.key].name
  }

  depends_on = [snowflake_schema.namespace]
}

resource "snowflake_grant_privileges_to_account_role" "lakehouse_future_schemas" {
  for_each = local.snowflake_layer_databases

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE ICEBERG TABLE"]

  on_schema {
    future_schemas_in_database = snowflake_database.layer[each.key].name
  }
}

resource "snowflake_grant_privileges_to_account_role" "lakehouse_all_tables" {
  for_each = local.snowflake_layer_databases

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE"]

  on_schema_object {
    all {
      object_type_plural = "ICEBERG TABLES"
      in_database        = snowflake_database.layer[each.key].name
    }
  }

  depends_on = [snowflake_schema.namespace]
}

resource "snowflake_grant_privileges_to_account_role" "lakehouse_future_tables" {
  for_each = local.snowflake_layer_databases

  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE"]

  on_schema_object {
    future {
      object_type_plural = "ICEBERG TABLES"
      in_database        = snowflake_database.layer[each.key].name
    }
  }
}

# A table's files are resolved through the external volume, so the role needs
# USAGE on it too — the database grant alone is not enough.
resource "snowflake_grant_privileges_to_account_role" "external_volume" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "EXTERNAL VOLUME"
    object_name = snowflake_external_volume.lakehouse.name
  }
}
