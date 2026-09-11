output "project_id" {
  description = "Project containing the monitor network."
  value       = local.project_id
}

output "subnetwork_id" {
  description = "Subnet to attach to the monitor VM."
  value       = google_compute_subnetwork.monitor.id
}

output "enable_iap_ssh" {
  description = "Whether the network permits IAP SSH to the monitor."
  value       = var.enable_iap_ssh
}
