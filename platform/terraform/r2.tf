# Nightly pg_dump output (ARCHITECTURE.md §11) and the mirrored Snowflake
# pipeline private key below. The publish-and-replicate DuckDB snapshot
# mechanism this bucket originally also held (ADR-0002) was retired in ADR-0012.
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
# backups and the mirrored Snowflake private key — mixing those sensitivity
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

# The Snowflake pipeline private key (snowflake.tf, ADR-0012), mirrored into
# the warehouse bucket as a private object. This is in addition to the
# snowflake_private_key output/repo-secret path, not a replacement for it — it
# exists for anything that can pull a Spaces object directly instead of going
# through gh secret + the deploy workflow. The bucket is private and this
# object carries no separate ACL override, but note it is still plaintext at
# rest: anyone with read access to the bucket can read the pipeline's key.
resource "digitalocean_spaces_bucket_object" "snowflake_pipeline_private_key" {
  region       = var.location
  bucket       = digitalocean_spaces_bucket.warehouse.name
  key          = "snowflake-pipeline-private-key.pem"
  content      = local.snowflake_private_key
  acl          = "private"
  content_type = "text/plain"
}
