locals {
  project_id = data.terraform_remote_state.dependencies["project-services"].outputs.project_id
}

resource "google_compute_network" "monitor" {
  project                 = local.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "monitor" {
  project                  = google_compute_network.monitor.project
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.monitor.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
  stack_type               = "IPV4_ONLY"
}

resource "google_compute_firewall" "iap_ssh" {
  count = var.enable_iap_ssh ? 1 : 0

  project       = local.project_id
  name          = "${var.name_prefix}-allow-iap-ssh"
  network       = google_compute_network.monitor.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["iap-ssh"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
