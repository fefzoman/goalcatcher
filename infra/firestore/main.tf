locals {
  # Keep the database close to the VM region. "(default)" is the database the
  # application client uses unless FIRESTORE_DATABASE_ID says otherwise.
  firestore_location    = local.region
  firestore_database_id = "(default)"
}

resource "google_firestore_database" "monitor" {
  project     = local.project_id
  name        = local.firestore_database_id
  location_id = local.firestore_location
  type        = "FIRESTORE_NATIVE"

  database_edition        = "STANDARD"
  concurrency_mode        = "PESSIMISTIC"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  deletion_policy         = "ABANDON"
}
