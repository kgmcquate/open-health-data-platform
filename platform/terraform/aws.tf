# AWS: storage only (ADR-0019). The Iceberg *catalog* is Snowflake — see
# snowflake.tf — but Snowflake has to keep its table files somewhere, and its
# external volumes accept S3, Azure and GCS, not DigitalOcean Spaces. So one
# bucket, one IAM role for Snowflake to assume, and nothing else catalog-shaped
# here: no Glue, no databases, no table metadata.
#
#   bucket -> the external volume's storage. Snowflake decides the layout
#             inside it; nothing else should write there except the pipeline's
#             dlt loads (see the IAM user below).
#
# The Snowflake role needs a second apply to become assumable; see
# `snowflake_storage_aws_*` in variables.tf and the README for the sequence.

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "lakehouse" {
  bucket = var.lakehouse_bucket
}

# The lakehouse is reproducible from public sources plus git (ARCHITECTURE.md
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

# Iceberg writes leave orphaned data files behind on rewrite (and dbt rebuilds
# tables wholesale). Nothing reads an aborted multipart upload.
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

# ---------------------------------------------------------------------------
# The pipeline's own AWS identity, for one job: dlt writes each raw table's
# Parquet into the volume's bucket itself, through fsspec, and fsspec cannot
# use the short-lived credentials Snowflake vends over the catalog. DuckDB
# (dbt) does take vended credentials and needs no key of its own.
#
# An IAM *user* with a long-lived access key, not a role: the pipeline runs in
# DigitalOcean Kubernetes, so there is no instance profile or OIDC trust to
# assume a role from. Rotate with
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
  statement {
    sid       = "LakehouseObjects"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.lakehouse.arn}/*"]
  }

  statement {
    sid       = "LakehouseBucket"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lakehouse.arn]
  }
}

resource "aws_iam_user_policy" "pipeline" {
  name   = "${var.name}-pipeline"
  user   = aws_iam_user.pipeline.name
  policy = data.aws_iam_policy_document.pipeline.json
}

# ---------------------------------------------------------------------------
# The role Snowflake assumes for the external volume. Read *and* write, unlike
# the Glue-catalog shape this replaced: Snowflake manages these tables now, so
# it is the one doing the compaction, the snapshot expiry and the metadata
# writes.
#
# Its trust policy names an IAM user ARN and external ID that Snowflake
# generates when it creates the external volume — so on a first apply the role
# is deliberately un-assumable and `snowflake_storage_aws_*` are empty. Run
# `DESC EXTERNAL VOLUME`, set the two variables, apply again. README has the
# exact commands.
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
  description        = "Assumed by Snowflake's external volume to manage lakehouse files in S3."
  assume_role_policy = data.aws_iam_policy_document.snowflake_storage_trust.json
}

data "aws_iam_policy_document" "snowflake_storage" {
  statement {
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["${aws_s3_bucket.lakehouse.arn}/*"]
  }

  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lakehouse.arn]
  }
}

resource "aws_iam_role_policy" "snowflake_storage" {
  name   = "${var.name}-snowflake-storage"
  role   = aws_iam_role.snowflake_storage.id
  policy = data.aws_iam_policy_document.snowflake_storage.json
}
