# Versioned DuckDB snapshots + the current.json pointer (ARCHITECTURE.md §3),
# and nightly pg_dump output (§11). Zero egress fees is the reason R2 is here
# rather than S3 — the replica pulls a full snapshot on every publish.
resource "cloudflare_r2_bucket" "warehouse" {
  account_id = var.cloudflare_account_id
  name       = var.snapshot_bucket
  location   = "EEUR"
}

# Separate bucket so a warehouse lifecycle rule can never reap state.
resource "cloudflare_r2_bucket" "tfstate" {
  account_id = var.cloudflare_account_id
  name       = "${var.name}-tfstate"
  location   = "EEUR"
}

# Snapshot retention (provisional 14 dailies — ARCHITECTURE.md §10.4) is applied
# by the publish job, not here: the Terraform provider does not manage R2
# lifecycle rules, and expiring snapshots is a pipeline concern anyway. See
# data/dagster/assets when that lands.
