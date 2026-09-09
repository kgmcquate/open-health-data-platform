terraform {
  required_version = ">= 1.9"

  required_providers {
    kamatera = {
      source  = "Kamatera/kamatera"
      version = "~> 0.9"
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

provider "kamatera" {
  api_client_id = var.kamatera_api_client_id
  api_secret    = var.kamatera_api_secret
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
