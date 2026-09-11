terraform {
  required_version = ">= 1.9"

  required_providers {
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
  }

  # Backend lives in backend.tf as a partial config.
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
  organization_name = var.snowflake_organization_name
  account_name      = var.snowflake_account_name
}
