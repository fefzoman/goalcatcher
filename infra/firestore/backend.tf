// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  backend "gcs" {
    bucket = "tf-state-goalcatcher"
    prefix = "firestore"
  }
}
