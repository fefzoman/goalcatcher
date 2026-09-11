# Exercise cross-stack outputs without contacting GCP or reading real state.
mock_provider "google" {}

variables {
  project_id = "goalcatcher-test"
}

override_data {
  target = data.terraform_remote_state.dependencies["networking"]
  values = {
    outputs = {
      subnetwork_id  = "projects/goalcatcher-test/regions/europe-central2/subnetworks/monitor-test"
      enable_iap_ssh = false
    }
  }
}

override_data {
  target = data.terraform_remote_state.dependencies["iam"]
  values = {
    outputs = {
      service_account_email = "monitor-test@goalcatcher-test.iam.gserviceaccount.com"
    }
  }
}

override_data {
  target = data.terraform_remote_state.dependencies["firestore"]
  values = {
    outputs = {
      firestore_database_id = "monitor-test"
    }
  }
}

run "uses_dependency_outputs" {
  command = plan

  assert {
    condition     = google_compute_instance.monitor.network_interface[0].subnetwork == "projects/goalcatcher-test/regions/europe-central2/subnetworks/monitor-test"
    error_message = "VM must use the networking stack's subnet."
  }

  assert {
    condition     = google_compute_instance.monitor.service_account[0].email == "monitor-test@goalcatcher-test.iam.gserviceaccount.com"
    error_message = "VM must use the IAM stack's service account."
  }

  assert {
    condition     = length(google_compute_instance.monitor.tags) == 0
    error_message = "VM SSH tags must follow the networking stack's output."
  }

  assert {
    condition     = strcontains(google_compute_instance.monitor.metadata_startup_script, base64encode("GOOGLE_CLOUD_PROJECT=goalcatcher-test\nFIRESTORE_DATABASE_ID=monitor-test\n"))
    error_message = "The startup script must pass the Firestore stack's database ID to systemd."
  }
}
