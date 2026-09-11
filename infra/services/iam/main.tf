locals {
  project_id = data.terraform_remote_state.dependencies["project-services"].outputs.project_id
}

resource "google_service_account" "monitor" {
  project      = local.project_id
  account_id   = "${var.name_prefix}-vm"
  display_name = "Football goal alert VM"
}

resource "google_project_iam_member" "monitor_firestore" {
  project = local.project_id
  role    = "roles/datastore.user"
  member  = google_service_account.monitor.member
}

resource "google_project_iam_member" "monitor_logging" {
  project = local.project_id
  role    = "roles/logging.logWriter"
  member  = google_service_account.monitor.member
}

resource "google_project_iam_member" "monitor_metrics" {
  project = local.project_id
  role    = "roles/monitoring.metricWriter"
  member  = google_service_account.monitor.member
}

resource "google_project_iam_member" "iap_tunnel" {
  for_each = var.enable_iap_ssh ? var.iap_ssh_members : toset([])

  project = local.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = each.value
}

resource "google_project_iam_member" "os_admin_login" {
  for_each = var.enable_iap_ssh ? var.iap_ssh_members : toset([])

  project = local.project_id
  role    = "roles/compute.osAdminLogin"
  member  = each.value
}

resource "google_project_iam_member" "operator_compute_viewer" {
  for_each = var.enable_iap_ssh ? var.iap_ssh_members : toset([])

  project = local.project_id
  role    = "roles/compute.viewer"
  member  = each.value
}

resource "google_service_account_iam_member" "operator_service_account_user" {
  for_each = var.enable_iap_ssh ? var.iap_ssh_members : toset([])

  service_account_id = google_service_account.monitor.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}
