output "service_account_email" {
  description = "Service account attached to the VM."
  value       = google_service_account.monitor.email
}

output "application_secret_ids" {
  description = "Secret Manager IDs read by the monitor at startup."
  value       = { for name, secret in google_secret_manager_secret.application : name => secret.secret_id }
}
