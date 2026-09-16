terraform {
  required_version = ">= 1.9"

  required_providers {
    # The lakehouse's storage: the S3 bucket behind Snowflake's external
    # volume, and the IAM identities that reach it (aws.tf, ADR-0019).
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
    # Note the source: the provider moved from Snowflake-Labs to snowflakedb at
    # v1. `Snowflake-Labs/snowflake` is the old, unmaintained address.
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "~> 2.21"
    }
    # Generates the pipeline's RSA key pair (snowflake.tf) — Snowflake SERVICE
    # users don't accept password auth; key-pair is the supported mechanism.
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Backend lives in backend.tf as a partial config.
}

# Credentials come from TF_VAR_aws_access_key_id / TF_VAR_aws_secret_access_key,
# NOT from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY: the S3 state backend
# (backend.tf) is DigitalOcean Spaces and reads *those* off the environment.
# Both are "S3", so leaving this provider to the default credential chain would
# hand it the Spaces key and every AWS call would fail confusingly.
#
# These are the admin credentials that create the bucket and the IAM
# identities — not the pipeline's own key, which aws.tf issues.
provider "aws" {
  region     = var.aws_region
  access_key = var.aws_access_key_id
  secret_key = var.aws_secret_access_key
}

provider "digitalocean" {}

# Reads the API token from the CLOUDFLARE_API_TOKEN environment variable.
provider "cloudflare" {}

# Everything except the account identifier comes from the environment, so no
# Snowflake credential is ever written to terraform.tfvars:
#
#   SNOWFLAKE_USER, SNOWFLAKE_ROLE=ACCOUNTADMIN, and one of
#   SNOWFLAKE_PRIVATE_KEY (key-pair, preferred) or SNOWFLAKE_PASSWORD.
#
# ACCOUNTADMIN (or a role with CREATE DATABASE / CREATE ROLE / CREATE USER /
# CREATE NETWORK POLICY) is required — this stack creates account-level objects.
provider "snowflake" {
  authenticator     = "SNOWFLAKE_JWT"
  organization_name = var.snowflake_organization_name
  account_name      = var.snowflake_account_name

  # Preview resources have to be opted into by name. `snowflake_database` and
  # the grant resources (snowflake.tf) are stable and need no entry here; the
  # external volume is the one lakehouse object the provider still ships as
  # preview.
  preview_features_enabled = [
    "snowflake_external_volume_resource",
    "snowflake_network_policy_attachment_resource",
  ]
}
