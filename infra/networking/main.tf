locals {
  # IPv4 CIDR assigned to the dedicated monitor subnet.
  subnet_cidr = "10.42.0.0/24"
}

resource "google_compute_network" "monitor" {
  project                 = local.project_id
  name                    = "${local.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "monitor" {
  project                  = google_compute_network.monitor.project
  name                     = "${local.name_prefix}-subnet"
  region                   = local.region
  network                  = google_compute_network.monitor.id
  ip_cidr_range            = local.subnet_cidr
  private_ip_google_access = true
  stack_type               = "IPV4_ONLY"
}

resource "google_compute_firewall" "iap_ssh" {
  count = local.enable_iap_ssh ? 1 : 0

  project       = local.project_id
  name          = "${local.name_prefix}-allow-iap-ssh"
  network       = google_compute_network.monitor.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["iap-ssh"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
