# Snowflake as a *reader* of the Iceberg lakehouse (ADR-0019, which supersedes
# ADR-0012's native tables and ADR-0013's per-layer databases). Nothing here
# stores or writes data any more: the tables live in S3 and the Glue catalog
# (aws.tf), dbt-duckdb writes them, and Snowflake is the query engine Cube's
# metrics and Streamlit's ad-hoc queries run on.
#
# Three objects make that work, in this order:
#
#   external volume      -> how Snowflake reads the S3 files
#   catalog integration  -> how Snowflake reads Glue's Iceberg REST endpoint
#   catalog-linked db    -> LAKEHOUSE, auto-synced from the catalog's namespaces
#
# All three are raw SQL through `snowflake_execute`: the provider has no
# `snowflake_catalog_integration` or catalog-linked-database resource as of
# v2.x, and its `snowflake_external_volume` is a preview resource. One
# mechanism for all three beats one resource plus two escape hatches.
#
# `snowflake_execute` runs `execute` on create and `revert` on destroy, and
# changing either forces a new resource — so each object carries its whole
# definition in `execute` and any change to a name, ARN or namespace list
# re-creates it. `CREATE OR REPLACE` rather than `CREATE IF NOT EXISTS` so that
# re-creation actually applies the new definition.

locals {
  # <organization>-<account>, the account identifier Cube and Streamlit connect
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

  snowflake_external_volume     = upper("${var.name}_lakehouse_vol")
  snowflake_catalog_integration = upper("${var.name}_glue_rest")

  # Equal to ohdp_ingestion.naming.CATALOG and to dbt's `attach.alias`
  # (data/dbt/profiles.yml). Keeping the three equal is what lets one compiled
  # dbt manifest name relations both DuckDB and Snowflake resolve — Cube builds
  # its SQL straight off that manifest (semantic/cube/model/globals.py).
  snowflake_linked_database = upper(var.lakehouse_catalog)
}

# Snowflake SERVICE users don't accept password auth (and PATs, which do, are
# only usable through Horizon's REST/OAuth2 path — not a plain SQL/JDBC/Python
# connector login, which is what Cube's and Streamlit's drivers both need).
# Key-pair is the supported mechanism: generate it here rather than requiring a
# human to run openssl and paste a public key into terraform.tfvars.
resource "tls_private_key" "pipeline" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

# ---------------------------------------------------------------------------
# Compute for Cube's and Streamlit's queries. XS, suspended after a minute, and
# created suspended: idle costs nothing. It no longer runs dbt builds, so if
# anything this is now over-provisioned rather than under.
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
# The query identity: one role, one SERVICE user, one RSA key pair. Read-only
# over the lakehouse — see the grants at the bottom of this file.
# ---------------------------------------------------------------------------
resource "snowflake_account_role" "pipeline" {
  name    = var.snowflake_pipeline_role
  comment = "Read-only on the LAKEHOUSE catalog-linked database, for Cube and Streamlit."
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
# locks out everyone. A NAT gateway with a stable IP is the prerequisite for
# making this real.
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
  comment        = "Cube and Streamlit. Authenticates with a key pair; reads the lakehouse, writes nothing."
  default_role   = snowflake_account_role.pipeline.name
  rsa_public_key = local.snowflake_pipeline_rsa_public_key

  # Both drivers connect with exactly one role; no secondary roles should come
  # along for the ride.
  default_secondary_roles_option = "NONE"
}

resource "snowflake_grant_account_role" "pipeline" {
  role_name = snowflake_account_role.pipeline.name
  user_name = snowflake_service_user.pipeline.name
}

# ---------------------------------------------------------------------------
# Reading the lakehouse.
# ---------------------------------------------------------------------------
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
      ALLOW_WRITES = FALSE
      COMMENT = 'OHDP Iceberg lakehouse files (ADR-0019). Read-only from Snowflake.';
  SQL

  revert = "DROP EXTERNAL VOLUME IF EXISTS ${local.snowflake_external_volume};"


}

resource "snowflake_execute" "catalog_integration" {
  # CATALOG_NAME is Glue's own name for the account-level catalog, which is the
  # AWS account ID — the same value dbt's `attach` path and dlt's pyiceberg
  # `warehouse` carry.
  execute = <<-SQL
    CREATE OR REPLACE CATALOG INTEGRATION ${local.snowflake_catalog_integration}
      CATALOG_SOURCE = ICEBERG_REST
      TABLE_FORMAT = ICEBERG
      CATALOG_NAMESPACE = 'core'
      REST_CONFIG = (
        CATALOG_URI = '${local.glue_rest_uri}'
        CATALOG_API_TYPE = AWS_GLUE
        CATALOG_NAME = '${data.aws_caller_identity.current.account_id}'
      )
      REST_AUTHENTICATION = (
        TYPE = SIGV4
        SIGV4_IAM_ROLE = '${aws_iam_role.snowflake_glue.arn}'
        SIGV4_SIGNING_REGION = '${var.aws_region}'
      )
      ENABLED = TRUE
      COMMENT = 'AWS Glue Iceberg REST catalog for the OHDP lakehouse (ADR-0019).';
  SQL

  revert = "DROP CATALOG INTEGRATION IF EXISTS ${local.snowflake_catalog_integration};"


}

# The catalog-linked database. Snowflake syncs the catalog's namespaces to
# schemas and its tables to Iceberg tables on its own, so there is no per-table
# DDL here and a new mart appears without a Terraform run.
#
# No NAMESPACE_MODE/identifier options: Glue is a case-insensitive catalog, and
# since Snowflake's 2026-04-30 change those default to CASE_INSENSITIVE, where
# unquoted `lakehouse.core.foo` resolves against the catalog's lowercase names.
# If a query ever comes back "object does not exist" with the name upper-cased
# in the error, that default is what to check first.
resource "snowflake_execute" "linked_database" {
  execute = <<-SQL
    CREATE OR REPLACE DATABASE ${local.snowflake_linked_database}
      LINKED_CATALOG = (
        CATALOG = '${local.snowflake_catalog_integration}',
        ALLOWED_NAMESPACES = (${join(", ", formatlist("'%s'", local.glue_namespaces))})
      )
      EXTERNAL_VOLUME = '${local.snowflake_external_volume}'
      COMMENT = 'Read-only view of the OHDP Iceberg lakehouse (ADR-0019).';
  SQL

  revert = "DROP DATABASE IF EXISTS ${local.snowflake_linked_database};"


  depends_on = [
    snowflake_execute.external_volume,
    snowflake_execute.catalog_integration,
  ]
}

# Read-only grants. `snowflake_execute` again rather than
# `snowflake_grant_privileges_to_account_role`: the provider's grant resources
# read back through SHOW GRANTS against a database it expects to manage, and a
# catalog-linked database's schemas appear and disappear as the catalog syncs.
#
# Note what is *not* here, relative to ADR-0012: no CREATE SCHEMA, no CREATE
# TABLE, no INSERT/UPDATE/DELETE/TRUNCATE. Snowflake writing to these tables
# would be a genuine conflict with dbt-duckdb's commits, not just an unused
# privilege.
resource "snowflake_execute" "lakehouse_grants" {
  execute = <<-SQL
    GRANT USAGE ON DATABASE ${local.snowflake_linked_database}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT USAGE ON ALL SCHEMAS IN DATABASE ${local.snowflake_linked_database}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT USAGE ON FUTURE SCHEMAS IN DATABASE ${local.snowflake_linked_database}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT SELECT ON ALL ICEBERG TABLES IN DATABASE ${local.snowflake_linked_database}
      TO ROLE ${snowflake_account_role.pipeline.name};
    GRANT SELECT ON FUTURE ICEBERG TABLES IN DATABASE ${local.snowflake_linked_database}
      TO ROLE ${snowflake_account_role.pipeline.name};
  SQL

  revert = <<-SQL
    REVOKE USAGE ON DATABASE ${local.snowflake_linked_database}
      FROM ROLE ${snowflake_account_role.pipeline.name};
  SQL


  depends_on = [snowflake_execute.linked_database]
}

# A query against an Iceberg table in the linked database resolves its files
# through the external volume, so the role needs USAGE on that too — the
# database grant alone is not enough.
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
