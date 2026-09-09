terraform {
  required_version = ">= 1.9"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.48"
    }
    cloudflare = {
      # NOTE: provider v5 renamed many resources (cloudflare_record ->
      # cloudflare_dns_record, etc.). This config targets v4. If you bump to v5,
      # expect to rewrite dns.tf and r2.tf.
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }
  }

  # State lives locally by default so a first `apply` needs no bootstrapping.
  # Once the R2 bucket exists, move state into it — R2 is S3-compatible, but the
  # S3 backend needs every AWS-specific check disabled:
  #
  # backend "s3" {
  #   bucket                      = "ohdp-tfstate"
  #   key                         = "platform/terraform.tfstate"
  #   region                      = "auto"
  #   endpoints                   = { s3 = "https://<accountid>.r2.cloudflarestorage.com" }
  #   skip_credentials_validation = true
  #   skip_metadata_api_check     = true
  #   skip_region_validation      = true
  #   skip_requesting_account_id  = true
  #   skip_s3_checksum            = true
  #   use_lockfile                = true
  # }
}

provider "hcloud" {
  token = var.hcloud_token
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
