locals {
  required_services = toset([
    "compute.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "sheets.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = local.project_id
  service            = each.value
  disable_on_destroy = false
}
