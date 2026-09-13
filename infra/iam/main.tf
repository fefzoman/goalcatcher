locals {
  # Operators only receive access while IAP SSH is enabled.
  operators = local.enable_iap_ssh ? toset(local.iap_ssh_members) : toset([])
  application_secrets = {
    API_FOOTBALL_KEY_SECRET_ID   = "${local.name_prefix}-api-football-key"
    TELEGRAM_BOT_TOKEN_SECRET_ID = "${local.name_prefix}-telegram-bot-token"
    TELEGRAM_CHAT_ID_SECRET_ID   = "${local.name_prefix}-telegram-chat-id"
  }
}

resource "google_service_account" "monitor" {
  project      = local.project_id
  account_id   = "${local.name_prefix}-vm"
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

resource "google_secret_manager_secret" "application" {
  for_each = local.application_secrets

  project   = local.project_id
  secret_id = each.value

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "monitor" {
  for_each = google_secret_manager_secret.application

  project   = local.project_id
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.monitor.member
}

resource "google_project_iam_member" "iap_tunnel" {
  for_each = local.operators

  project = local.project_id
  role    = "roles/iap.tunnelResourceAccessor"
  member  = each.value
}

resource "google_project_iam_member" "os_admin_login" {
  for_each = local.operators

  project = local.project_id
  role    = "roles/compute.osAdminLogin"
  member  = each.value
}

resource "google_project_iam_member" "operator_compute_viewer" {
  for_each = local.operators

  project = local.project_id
  role    = "roles/compute.viewer"
  member  = each.value
}

resource "google_service_account_iam_member" "operator_service_account_user" {
  for_each = local.operators

  service_account_id = google_service_account.monitor.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}
