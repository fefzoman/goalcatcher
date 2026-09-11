locals {
  project_id = data.terraform_remote_state.dependencies["project-services"].outputs.project_id
}

resource "google_firestore_database" "monitor" {
  project     = local.project_id
  name        = var.firestore_database_id
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"

  database_edition        = "STANDARD"
  concurrency_mode        = "PESSIMISTIC"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"
}
