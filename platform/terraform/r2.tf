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
