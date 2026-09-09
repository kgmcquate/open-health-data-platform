# Partial backend config. Values come from `-backend-config` flags so no account
# ID or bucket name is committed, and so `terraform init -backend=false` still
# works for a local `validate`.
#
# One-time bootstrap — the state bucket is not managed by Terraform (see r2.tf):
#
#   1. Create an R2 bucket named `ohdp-tfstate` in the Cloudflare dashboard.
#   2. Create an R2 API token (Object Read & Write) and note its access key pair.
#   3. Init against it:
#
#      terraform init \
#        -backend-config="bucket=ohdp-tfstate" \
#        -backend-config="key=platform/terraform.tfstate" \
#        -backend-config="endpoints={s3=\"https://<accountid>.r2.cloudflarestorage.com\"}" \
#        -backend-config="region=auto" \
#        -backend-config="skip_credentials_validation=true" \
#        -backend-config="skip_metadata_api_check=true" \
#        -backend-config="skip_region_validation=true" \
#        -backend-config="skip_requesting_account_id=true" \
#        -backend-config="skip_s3_checksum=true" \
#        -backend-config="use_lockfile=true"
#
# R2 is S3-compatible but not S3: every AWS-specific probe above must be skipped,
# and `skip_s3_checksum` is required or uploads fail. `use_lockfile=true` gives
# state locking without a DynamoDB table.
#
# The deploy-infra workflow passes all of this from secrets.
terraform {
  backend "s3" {}
}
