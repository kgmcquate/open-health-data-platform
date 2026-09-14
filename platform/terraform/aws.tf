# The Iceberg lakehouse: S3 for the files, the Glue Data Catalog for the
# metadata (ADR-0019). AWS is here — rather than DigitalOcean Spaces, where the
# rest of this project's object storage lives — because Snowflake's external
# volumes accept S3, Azure and GCS only, and Snowflake has to be able to read
# these tables for Cube and Streamlit.
#
#   bucket    -> every table's data + metadata files, one prefix per namespace
#   namespace -> a Glue database: raw_<source>, clean_<source>, core, mart_<name>
#   table     -> an Iceberg table, written by dlt (raw) or dbt-duckdb (the rest)
#
# Two identities are created here:
#
#   * the pipeline's IAM user, whose access key dlt/pyiceberg/DuckDB all use;
#   * two roles Snowflake assumes — one for the external volume's S3 access,
#     one to SigV4-sign its calls to Glue's Iceberg REST endpoint.
#
# The Snowflake roles need a second apply to become usable; see
# `snowflake_storage_aws_*` in variables.tf and the README for the sequence.

locals {
  # Glue namespaces, flat and lowercase (Glue's own constraint), with the
  # medallion layer as a prefix. Must match ohdp_ingestion.naming.schema() —
  # that module, not this file, is what the pipeline reads at runtime, so a
  # rename here has to be mirrored there (and in dbt's
  # macros/generate_schema_name.sql, which derives the same names from model
  # names).
  #
  # dlt and dbt-duckdb both create a missing namespace themselves, so this list
  # is about making the structure legible in the Glue console and grantable in
  # IAM, not about unblocking the pipeline.
  glue_namespaces = concat(
    [for s in var.lakehouse_sources : "raw_${s}"],
    [for s in var.lakehouse_sources : "clean_${s}"],
    ["core"],
    [for m in var.lakehouse_marts : "mart_${m}"],
  )

  # Glue's Iceberg REST endpoint. The same URI dlt's pyiceberg catalog, DuckDB's
  # ATTACH and Snowflake's catalog integration each point at.
  glue_rest_uri = "https://glue.${var.aws_region}.amazonaws.com/iceberg"

  lakehouse_bucket_arn = aws_s3_bucket.lakehouse.arn
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "lakehouse" {
  bucket = var.lakehouse_bucket
}

# The warehouse is reproducible from public sources plus git (ARCHITECTURE.md
# §11), so there is nothing here worth versioning — and Iceberg keeps its own
# snapshot history for the time-travel case anyway.
resource "aws_s3_bucket_public_access_block" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Iceberg writes leave orphaned data files behind on rewrite (and dlt/dbt both
# rebuild tables wholesale). Nothing reads an aborted multipart upload, and
# expired snapshots' files are unreferenced — this keeps neither around for long.
resource "aws_s3_bucket_lifecycle_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Dagster's S3ComputeLogManager (platform/helm/values/dagster.yaml). On AWS
# rather than Spaces because that log manager takes no access-key config — it
# reads AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY off the process environment,
# which in the pipeline pod now has to hold the real AWS credentials pyiceberg's
# SigV4 signer needs (ADR-0019). Two S3 credentials can't coexist there.
#
# Private, like the Spaces bucket it replaces: the webserver proxies objects
# server-side rather than handing out public URLs. Expiration mirrors the
# Postgres run-history retention window (`retention.schedule.purgeAfterDays: 30`
# in dagster.yaml) so it doesn't grow unbounded.
resource "aws_s3_bucket" "compute_logs" {
  bucket = var.compute_logs_bucket
}

resource "aws_s3_bucket_public_access_block" "compute_logs" {
  bucket = aws_s3_bucket.compute_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "compute_logs" {
  bucket = aws_s3_bucket.compute_logs.id

  rule {
    id     = "expire"
    status = "Enabled"

    filter {}

    expiration {
      days = 30
    }
  }
}

# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
resource "aws_glue_catalog_database" "namespace" {
  for_each = toset(local.glue_namespaces)

  name        = each.value
  description = "OHDP lakehouse namespace ${each.value} (ADR-0019)."
}

# ---------------------------------------------------------------------------
# The pipeline identity. An IAM *user* with a long-lived access key, not a
# role: the pipeline runs in DigitalOcean Kubernetes, so there is no instance
# profile or OIDC trust to assume a role from. Rotate with
# `terraform apply -replace=aws_iam_access_key.pipeline`, then re-publish the
# two repo secrets (see outputs.tf).
# ---------------------------------------------------------------------------
resource "aws_iam_user" "pipeline" {
  name = "${var.name}-pipeline"
  tags = { project = var.name }
}

resource "aws_iam_access_key" "pipeline" {
  user = aws_iam_user.pipeline.name
}

data "aws_iam_policy_document" "pipeline" {
  # The lakehouse itself: dlt and dbt-duckdb both read and write table files.
  statement {
    sid       = "LakehouseObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.lakehouse_bucket_arn}/*"]
  }

  statement {
    sid       = "LakehouseBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.lakehouse_bucket_arn]
  }

  # Dagster's compute logs.
  statement {
    sid       = "ComputeLogObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.compute_logs.arn}/*"]
  }

  statement {
    sid       = "ComputeLogBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.compute_logs.arn]
  }

  # The catalog. CreateDatabase is what lets dlt open a new `raw_<source>`
  # namespace and dbt-duckdb a new `mart_<name>` without a Terraform run, the
  # same way the Snowflake role used to carry CREATE SCHEMA.
  statement {
    sid = "GlueCatalog"
    actions = [
      "glue:GetCatalog",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:CreateDatabase",
      "glue:GetTable",
      "glue:GetTables",
      "glue:CreateTable",
      "glue:UpdateTable",
      "glue:DeleteTable",
    ]
    resources = [
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/*/*",
    ]
  }
}

resource "aws_iam_user_policy" "pipeline" {
  name   = "${var.name}-pipeline"
  user   = aws_iam_user.pipeline.name
  policy = data.aws_iam_policy_document.pipeline.json
}

# ---------------------------------------------------------------------------
# The two roles Snowflake assumes. Both are read-only: nothing in Snowflake
# writes to the lakehouse (ADR-0019).
#
# Their trust policies name an IAM user ARN and external ID that Snowflake
# generates when it creates the external volume / catalog integration — so on a
# first apply they are deliberately un-assumable, and `snowflake_storage_aws_*`
# / `snowflake_glue_aws_*` are empty. Run `DESC EXTERNAL VOLUME` and
# `DESC CATALOG INTEGRATION`, set the four variables, apply again. README has
# the exact commands.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "snowflake_storage_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type = "AWS"
      # Self until Snowflake's own IAM user ARN is known: a role has to trust
      # something, and trusting this account grants nothing new.
      identifiers = [
        var.snowflake_storage_aws_iam_user_arn != ""
        ? var.snowflake_storage_aws_iam_user_arn
        : data.aws_caller_identity.current.arn
      ]
    }

    dynamic "condition" {
      for_each = var.snowflake_storage_aws_external_id != "" ? [1] : []

      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.snowflake_storage_aws_external_id]
      }
    }
  }
}

resource "aws_iam_role" "snowflake_storage" {
  name               = "${var.name}-snowflake-storage"
  description        = "Assumed by Snowflake's external volume to read lakehouse files from S3."
  assume_role_policy = data.aws_iam_policy_document.snowflake_storage_trust.json
}

data "aws_iam_policy_document" "snowflake_storage" {
  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${local.lakehouse_bucket_arn}/*"]
  }

  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.lakehouse_bucket_arn]
  }
}

resource "aws_iam_role_policy" "snowflake_storage" {
  name   = "${var.name}-snowflake-storage"
  role   = aws_iam_role.snowflake_storage.id
  policy = data.aws_iam_policy_document.snowflake_storage.json
}

data "aws_iam_policy_document" "snowflake_glue_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type = "AWS"
      identifiers = [
        var.snowflake_glue_aws_iam_user_arn != ""
        ? var.snowflake_glue_aws_iam_user_arn
        : data.aws_caller_identity.current.arn
      ]
    }

    dynamic "condition" {
      for_each = var.snowflake_glue_aws_external_id != "" ? [1] : []

      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.snowflake_glue_aws_external_id]
      }
    }
  }
}

resource "aws_iam_role" "snowflake_glue" {
  name               = "${var.name}-snowflake-glue"
  description        = "Assumed by Snowflake's catalog integration to read the Glue Iceberg REST endpoint."
  assume_role_policy = data.aws_iam_policy_document.snowflake_glue_trust.json
}

data "aws_iam_policy_document" "snowflake_glue" {
  statement {
    actions = [
      "glue:GetCatalog",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
    ]
    resources = [
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/*/*",
    ]
  }
}

resource "aws_iam_role_policy" "snowflake_glue" {
  name   = "${var.name}-snowflake-glue"
  role   = aws_iam_role.snowflake_glue.id
  policy = data.aws_iam_policy_document.snowflake_glue.json
}
