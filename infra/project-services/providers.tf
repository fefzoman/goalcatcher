# tflint-ignore-file: terraform_unused_declarations
// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

locals {
  terraform-git-repo = "goalcatcher"
}

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
}

provider "google" {
  project = "replace-me"
  region  = "europe-central2"
  zone    = "europe-central2-a"

  default_labels = {
    environment = "production"
    project     = "goalcatcher"
    managed-by  = "terraform"
  }
}
