locals {
  app_directory = "/opt/football-goal-alert"

  machine_type      = "e2-micro"
  boot_disk_size_gb = 10

  # Set to false and apply that change before an intentional destroy.
  deletion_protection = true

  # Environment, project and managed-by labels come from the provider's
  # default_labels (see providers.tf).
  labels = {
    application = "football-goal-alert"
  }
}

resource "google_compute_instance" "monitor" {
  project      = local.project_id
  name         = local.name_prefix
  zone         = local.zone
  machine_type = local.machine_type

  allow_stopping_for_update = true
  can_ip_forward            = false
  deletion_protection       = local.deletion_protection
  tags                      = data.terraform_remote_state.networking.outputs.enable_iap_ssh ? ["iap-ssh"] : []
  labels                    = local.labels

  boot_disk {
    auto_delete = true

    initialize_params {
      image = "debian-cloud/debian-12"
      size  = local.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = data.terraform_remote_state.networking.outputs.subnetwork_id

    # The ephemeral public address provides low-cost outbound Internet access.
    # There are no public ingress firewall rules; administration uses IAP.
    access_config {
      network_tier = "STANDARD"
    }
  }

  metadata = {
    block-project-ssh-keys = "TRUE"
    enable-oslogin         = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/templates/startup.sh.tftpl", {
    app_directory = local.app_directory
    application_environment_base64 = base64encode(join("\n", [
      "GOOGLE_CLOUD_PROJECT=${local.project_id}",
      "FIRESTORE_DATABASE_ID=${data.terraform_remote_state.firestore.outputs.firestore_database_id}",
      "",
    ]))
    service_unit_base64 = base64encode(file("${path.module}/../../deploy/football-goal-alert.service"))
  })

  service_account {
    email = data.terraform_remote_state.iam.outputs.service_account_email
    scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
      "https://www.googleapis.com/auth/datastore",
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring.write",
      "https://www.googleapis.com/auth/spreadsheets",
    ]
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    provisioning_model  = "STANDARD"
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_secure_boot          = true
    enable_vtpm                 = true
  }
}
