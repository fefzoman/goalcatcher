# tflint-ignore-file: terraform_unused_declarations
// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.0"
    }
  }
}

provider "google" {
  project = "goalcatcher-508312"
  region  = "europe-central2"
  zone    = "europe-central2-a"

  default_labels = {
    managed-by = "terraform"
  }
}
