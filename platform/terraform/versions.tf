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

  # Backend lives in backend.tf as a partial config.
}

provider "hcloud" {
  token = var.hcloud_token
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
