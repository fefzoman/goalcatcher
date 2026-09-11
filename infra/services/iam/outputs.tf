output "service_account_email" {
  description = "Service account attached to the VM."
  value       = google_service_account.monitor.email
}
