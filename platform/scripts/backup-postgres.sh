#!/usr/bin/env bash
# Nightly pg_dump of the single Postgres instance to Spaces (ARCHITECTURE.md §11).
# Covers: dagster, superset, openmetadata, app databases.
# Stub — restore path must be tested before M4.
set -euo pipefail
echo "TODO(M1): pg_dumpall | gzip | aws s3 cp - s3://\$OHDP_SPACES_BUCKET/backups/pg-\$(date -u +%FT%TZ).sql.gz"
