# Nightly pg_dump output (ARCHITECTURE.md §11) and the mirrored Snowflake
# credentials below — the private key and the Horizon catalog token. The publish-and-replicate DuckDB snapshot mechanism this
# bucket originally also held (ADR-0002) was retired in ADR-0012. The lakehouse
# itself lives on AWS S3 (aws.tf, ADR-0019) because Snowflake cannot keep
# Iceberg files on Spaces — but nothing else moved: compute logs are still
# here, since the pipeline authenticates to Snowflake's catalog with a token
# rather than with AWS credentials, so the two object stores never contend for
# boto3's environment variables.
resource "digitalocean_spaces_bucket" "warehouse" {
  name   = var.snapshot_bucket
  region = var.location
  acl    = "private"
}

# The state bucket is deliberately NOT managed here. Terraform storing its own
# state in a bucket it also creates is a bootstrap cycle: destroying the bucket
# destroys the record of the bucket. Create the Space once by hand and then
# point the backend at it as described in backend.tf.

# Dagster's S3ComputeLogManager (platform/helm/values/dagster.yaml). Kept
# separate from the `warehouse` bucket above on purpose: compute logs are the
# one thing here meant to be publicly browsable (docs/ARCHITECTURE.md's
# world-readable-run-logs policy), while `warehouse` holds private Postgres
# backups and the mirrored Snowflake credentials — mixing those sensitivity
# levels in one bucket would undo the point of the ACL. Private ACL is still
# correct even so: the webserver proxies objects server-side rather than
# handing out public URLs (S3ComputeLogManager's `show_url_only` stays unset).
# Expiration mirrors the existing Postgres run-history retention window
# (`retention.schedule.purgeAfterDays: 30` in dagster.yaml) so this doesn't
# grow unbounded.
resource "digitalocean_spaces_bucket" "compute_logs" {
  name   = var.compute_logs_bucket
  region = var.location
  acl    = "private"

  lifecycle_rule {
    enabled = true
    expiration {
      days = 30
    }
  }
}

# The Snowflake query role's private key (snowflake.tf, ADR-0019), mirrored
# into the warehouse bucket as a private object. This is in addition to the
# snowflake_private_key output/repo-secret path, not a replacement for it — it
# exists for anything that can pull a Spaces object directly instead of going
# through gh secret + the deploy workflow. The bucket is private and this
# object carries no separate ACL override, but note it is still plaintext at
# rest: anyone with read access to the bucket can read the key.
resource "digitalocean_spaces_bucket_object" "snowflake_pipeline_private_key" {
  region       = var.location
  bucket       = digitalocean_spaces_bucket.warehouse.name
  key          = "snowflake-pipeline-private-key.pem"
  content      = local.snowflake_private_key
  acl          = "private"
  content_type = "text/plain"
}

# The pipeline's Horizon catalog token (snowflake.tf), mirrored the same way and
# for the same reason as the key above. It earns the mirror more than the key
# does: `days_to_expiry` caps the PAT at a year, so this is the credential that
# actually gets rotated, and rotation depends on a hand-run `gh secret set`.
# Skipping that is silent — an unset repo secret expands to "" in
# deploy-platform.yml, `kubectl create secret --from-literal` accepts the empty
# string, and every dbt run then dies at Horizon's token endpoint with
# `invalid_client` rather than with anything naming the missing token.
#
# What makes that safe to plan in CI: deploy-infra.yml pipes the whole plan into
# the job summary, readable by anyone with Actions access, and the token would
# be the one credential printed there in clear text. It is not, because the
# provider marks `token` sensitive and Terraform carries that mark through the
# reference — `content` itself is an unmarked attribute, so the redaction is
# inherited, not intrinsic. `sensitive()` restates it at the point of use so a
# provider that ever stops marking the attribute cannot quietly un-redact it.
#
# No trailing newline, unlike the PEM above — whatever pipes this object into a
# header or a Secret should not have to strip one.
resource "digitalocean_spaces_bucket_object" "snowflake_pipeline_horizon_pat" {
  region       = var.location
  bucket       = digitalocean_spaces_bucket.warehouse.name
  key          = "snowflake-pipeline-horizon-pat.txt"
  content      = sensitive(snowflake_user_programmatic_access_token.pipeline.token)
  acl          = "private"
  content_type = "text/plain"
}
