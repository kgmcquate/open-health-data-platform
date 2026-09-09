terraform {
  backend "s3" {
    bucket = "6527aae0-e578-4217-afd0-cb04219704a6-terraform-state"
    key    = "platform/terraform.tfstate"
    region = "us-east-1"
    endpoints = {
      s3 = "https://nyc3.digitaloceanspaces.com"
    }

    force_path_style            = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
  }
}
