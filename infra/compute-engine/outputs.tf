output "instance_name" {
  description = "Name of the monitor VM."
  value       = google_compute_instance.monitor.name
}

output "instance_zone" {
  description = "Zone containing the monitor VM."
  value       = google_compute_instance.monitor.zone
}

output "public_ip" {
  description = "Ephemeral address used for outbound Internet access. No public ingress rule is created."
  value       = google_compute_instance.monitor.network_interface[0].access_config[0].nat_ip
}

output "iap_ssh_command" {
  description = "Command for connecting to the VM through IAP."
  value       = "gcloud compute ssh ${google_compute_instance.monitor.name} --project=${local.project_id} --zone=${local.zone} --tunnel-through-iap"
}
