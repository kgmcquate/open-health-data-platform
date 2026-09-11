# Versioned DuckDB snapshots + the current.json pointer (ARCHITECTURE.md §3),
# and nightly pg_dump output (§11). DigitalOcean Spaces uses the same S3-style
# access pattern without the Cloudflare dependency.
resource "digitalocean_spaces_bucket" "warehouse" {
  name   = var.snapshot_bucket
  region = var.location
  acl    = "private"
}

# The state bucket is deliberately NOT managed here. Terraform storing its own
# state in a bucket it also creates is a bootstrap cycle: destroying the bucket
# destroys the record of the bucket. Create the Space once by hand and then
# point the backend at it as described in backend.tf.

# The Horizon Catalog credential (snowflake.tf, ADR-0011), mirrored into the
# warehouse bucket as a private object. This is in addition to the
# iceberg_credential output/repo-secret path, not a replacement for it — it
# exists for anything that can pull a Spaces object directly instead of going
# through gh secret + the deploy workflow. The bucket is private and this
# object carries no separate ACL override, but note it is still plaintext at
# rest: anyone with read access to the bucket can read the pipeline's PAT.
resource "digitalocean_spaces_bucket_object" "iceberg_credential" {
  region       = var.location
  bucket       = digitalocean_spaces_bucket.warehouse.name
  key          = "iceberg-credential.txt"
  content      = local.iceberg_credential
  acl          = "private"
  content_type = "text/plain"
}
