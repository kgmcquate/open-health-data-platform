# Snowflake Horizon Catalog — the Iceberg REST catalog for the medallion lake
# (ADR-0011, which supersedes the Polaris half of ADR-0010).
#
# Horizon exposes an Iceberg REST endpoint at
#   https://<organization>-<account>.snowflakecomputing.com/polaris/api/catalog
# that pyiceberg attaches to directly, so dlt, the dbt-duckdb plugin and the
# publish step all keep talking plain `pyiceberg.catalog`. DuckDB is still the
# only compute — Snowflake here is a catalog and a storage layer, not an engine.
#
# The mapping the REST protocol imposes:
#
#   Iceberg `warehouse`  -> the Snowflake DATABASE below
#   Iceberg namespace    -> a SCHEMA in that database
#   Iceberg table        -> an Iceberg TABLE in that schema
#
# Storage is Snowflake-managed: `external_volume` is deliberately left unset, so
# tables land in Snowflake's own storage and Horizon vends short-lived
# credentials to the REST client. That is what removes the whole Polaris
# workaround — no storage keys handed to the catalog, no suppressed
# `X-Iceberg-Access-Delegation` header, no bucket policy. Note this is forced
# rather than merely preferred: Snowflake's external-engine path does not
# support S3-compatible non-AWS storage, so DigitalOcean Spaces cannot hold
# these tables. Spaces keeps the DuckDB snapshots and pg_dump backups (r2.tf).

locals {
  # <organization>-<account>, the account identifier in URLs.
  snowflake_account_identifier = "${var.snowflake_organization_name}-${var.snowflake_account_name}"
  snowflake_catalog_uri        = "https://${local.snowflake_account_identifier}.snowflakecomputing.com/polaris/api/catalog"

  # "<user>:<pat>" — the client_credentials pair pyiceberg exchanges for a
  # token. Shared by the iceberg_credential output and the Spaces object in
  # r2.tf so there is exactly one place this gets assembled.
  iceberg_credential = "${snowflake_service_user.pipeline.name}:${snowflake_user_programmatic_access_token.pipeline.token}"
}

# ---------------------------------------------------------------------------
# The catalog: one database, one schema per Iceberg namespace.
#
# IDENTIFIER CASING — the thing that will bite you. The REST protocol passes
# namespace and table names through verbatim, and the pipeline speaks lowercase
# (`raw_healthdata_gov`, and dbt model names like `stg_healthdata_gov_datasets`).
# So these schemas are created lowercase and Snowflake stores them as quoted
# lowercase identifiers. The pipeline needs no casing translation; the cost is
# that ad-hoc SQL in Snowsight must quote them:
#
#   select * from ohdp."clean_healthdata_gov"."stg_datasets";
#
# The database is uppercase because it is only ever named through the REST
# `warehouse` property and in SQL, never built from a Python identifier.
# ---------------------------------------------------------------------------
resource "snowflake_database" "ohdp" {
  name    = upper(var.snowflake_database)
  comment = "OHDP Iceberg lakehouse — the `warehouse` an Iceberg REST client attaches to (ADR-0011)."

  # Snowflake-managed storage. `external_volume` unset means the tables use
  # Snowflake storage unless the *account* carries a default external volume —
  # if one is ever set there, set it to "SNOWFLAKE_MANAGED" here to pin it.
  data_retention_time_in_days = var.snowflake_data_retention_days
}

resource "snowflake_schema" "namespace" {
  for_each = toset(var.snowflake_namespaces)

  database = snowflake_database.ohdp.name
  name     = upper(each.value)
  comment  = "Iceberg namespace ${each.value} (ADR-0010 medallion layer)."

  # Marts arrive over time and the dbt plugin calls create_namespace_if_not_exists,
  # so a namespace can exist before it is listed in `snowflake_namespaces`. Adding
  # it to the variable later adopts it rather than fighting over it.
  lifecycle {
    ignore_changes = [comment]
  }
}

# ---------------------------------------------------------------------------
# Compute. Not needed for the REST catalog path — DuckDB does the work and
# metadata operations run warehouse-free — but ad-hoc SQL and Snowsight need
# one. XS, suspended after a minute, and created suspended: idle costs nothing.
# ---------------------------------------------------------------------------
resource "snowflake_warehouse" "ohdp" {
  name                = upper(var.snowflake_warehouse)
  warehouse_size      = "XSMALL"
  auto_suspend        = 60
  auto_resume         = true
  initially_suspended = true
  comment             = "Ad-hoc SQL over the OHDP lake. The pipeline does not use it — DuckDB is the compute."
}

# ---------------------------------------------------------------------------
# The pipeline identity: one role, one SERVICE user, one PAT.
# ---------------------------------------------------------------------------
resource "snowflake_account_role" "pipeline" {
  name    = var.snowflake_pipeline_role
  comment = "Read/write on the OHDP lake for the Dagster pipeline (dlt raw loads, dbt models, publish)."
}

# USAGE lets the role see the database; CREATE SCHEMA lets the dbt plugin's
# create_namespace_if_not_exists open a new `mart_<name>` without a Terraform run.
resource "snowflake_grant_privileges_to_account_role" "database" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE SCHEMA"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.ohdp.name
  }
}

# Existing and future namespaces. CREATE ICEBERG TABLE is what lets pyiceberg
# issue a REST createTable; USAGE is what lets it resolve the namespace at all.
resource "snowflake_grant_privileges_to_account_role" "schemas_existing" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE ICEBERG TABLE"]

  on_schema {
    all_schemas_in_database = snowflake_database.ohdp.fully_qualified_name
  }

  depends_on = [snowflake_schema.namespace]
}

resource "snowflake_grant_privileges_to_account_role" "schemas_future" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["USAGE", "CREATE ICEBERG TABLE"]

  on_schema {
    future_schemas_in_database = snowflake_database.ohdp.fully_qualified_name
  }
}

# Tables the role did not create itself. A table created over REST is owned by
# this role, which already has everything on it; these grants cover tables made
# in Snowsight or by a future second identity. The full DML set is required —
# Snowflake checks SELECT, INSERT, UPDATE, DELETE *and* TRUNCATE for an external
# engine write, not just the one the statement looks like.
resource "snowflake_grant_privileges_to_account_role" "tables_existing" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]

  on_schema_object {
    all {
      object_type_plural = "ICEBERG TABLES"
      in_database        = snowflake_database.ohdp.fully_qualified_name
    }
  }

  depends_on = [snowflake_schema.namespace]
}

resource "snowflake_grant_privileges_to_account_role" "tables_future" {
  account_role_name = snowflake_account_role.pipeline.name
  privileges        = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]

  on_schema_object {
    future {
      object_type_plural = "ICEBERG TABLES"
      in_database        = snowflake_database.ohdp.fully_qualified_name
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
# Network policy. Not optional decoration: Snowflake refuses to *generate or
# use* a PAT for a SERVICE user that is not subject to one.
#
# It has to be attached at the ACCOUNT level, not the user level: Snowflake's
# own Horizon Catalog docs for external-engine access say plainly "Using
# network policies that are set at the user level isn't supported with this
# feature" — a user-level `network_policy` on the service user is exactly what
# was on `snowflake_service_user.pipeline` before, and it makes every
# session:role token exchange fail OAuth scope validation with
# `invalid_scope: The scope is invalid` even though the role/grants are fine.
# PATs accept either an account- or a user-level policy to satisfy their own
# "must be subject to a policy" requirement, so account-level is the one
# setting that satisfies both.
#
# CONSEQUENCE OF GOING ACCOUNT-WIDE: `snowflake_allowed_ips` now gates every
# session in the account, human logins included, not just this service user.
# The default allows everything, which satisfies the requirement without
# pretending to be a control. Narrow it the moment you know the egress IP the
# DOKS nodes present — but know that DigitalOcean node public IPs change when a
# node is recycled or the pool is resized, and now a stale entry locks out
# *everyone*, not just breaks the pipeline. A NAT gateway with a stable IP is
# the prerequisite for making this real.
# ---------------------------------------------------------------------------
resource "snowflake_network_policy" "pipeline" {
  name            = upper("${var.snowflake_pipeline_role}_NETWORK_POLICY")
  allowed_ip_list = var.snowflake_allowed_ips
  comment         = "Required for PAT auth account-wide (Horizon Catalog external-engine access rejects a user-level policy)."
}

resource "snowflake_network_policy_attachment" "pipeline" {
  network_policy_name = snowflake_network_policy.pipeline.name
  set_for_account     = true
}

resource "snowflake_service_user" "pipeline" {
  name         = upper("${var.snowflake_pipeline_user}")
  comment      = "Dagster pipeline. Authenticates to the Horizon Catalog REST endpoint with a PAT."
  default_role = snowflake_account_role.pipeline.name

  # The catalog client asks for exactly one role (`session:role:<role>`); no
  # secondary roles should come along for the ride.
  default_secondary_roles_option = "NONE"
}

resource "snowflake_grant_account_role" "pipeline" {
  role_name = snowflake_account_role.pipeline.name
  user_name = snowflake_service_user.pipeline.name
}

# The credential the pipeline actually ships with. pyiceberg does an OAuth2
# client_credentials exchange against the catalog's /v1/oauth/tokens, so the
# `credential` property is "<user>:<token>" — see the iceberg_credential output.
#
# Rotation: change `snowflake_pat_keeper` to any new non-empty value and apply.
# Snowflake caps a PAT at 365 days, so this is a standing calendar item — an
# expired token fails every asset in the run with a 401.
resource "snowflake_user_programmatic_access_token" "pipeline" {
  user             = snowflake_service_user.pipeline.name
  name             = "OHDP_PIPELINE_CATALOG"
  role_restriction = snowflake_account_role.pipeline.name
  days_to_expiry   = var.snowflake_pat_days_to_expiry
  keeper           = var.snowflake_pat_keeper
  comment          = "Iceberg REST catalog access for the Dagster pipeline."

  # The role must be granted to the user before a role-restricted token on it
  # can be issued, and the account-wide network policy must already be in
  # effect before Snowflake will issue or accept a PAT at all.
  depends_on = [snowflake_grant_account_role.pipeline, snowflake_network_policy_attachment.pipeline]
}
