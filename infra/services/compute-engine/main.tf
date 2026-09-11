locals {
  app_directory = "/opt/football-goal-alert"
  labels = merge(
    {
      application = "football-goal-alert"
      managed-by  = "terraform"
    },
    var.labels,
  )
}

resource "google_compute_instance" "monitor" {
  project      = var.project_id
  name         = var.name_prefix
  zone         = var.zone
  machine_type = var.machine_type

  allow_stopping_for_update = true
  can_ip_forward            = false
  deletion_protection       = var.deletion_protection
  tags                      = data.terraform_remote_state.dependencies["networking"].outputs.enable_iap_ssh ? ["iap-ssh"] : []
  labels                    = local.labels

  boot_disk {
    auto_delete = true

    initialize_params {
      image = "debian-cloud/debian-12"
      size  = var.boot_disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = data.terraform_remote_state.dependencies["networking"].outputs.subnetwork_id

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
      "GOOGLE_CLOUD_PROJECT=${var.project_id}",
      "FIRESTORE_DATABASE_ID=${data.terraform_remote_state.dependencies["firestore"].outputs.firestore_database_id}",
      "",
    ]))
    service_unit_base64 = base64encode(file("${path.module}/../../../deploy/football-goal-alert.service"))
  })

  service_account {
    email = data.terraform_remote_state.dependencies["iam"].outputs.service_account_email
    scopes = [
      "https://www.googleapis.com/auth/datastore",
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring.write",
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
