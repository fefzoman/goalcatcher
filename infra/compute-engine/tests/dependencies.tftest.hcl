# Exercise cross-stack outputs without contacting GCP or reading real state.
mock_provider "google" {}

override_data {
  target = data.terraform_remote_state.networking
  values = {
    outputs = {
      subnetwork_id  = "projects/goalcatcher-test/regions/europe-central2/subnetworks/monitor-test"
      enable_iap_ssh = false
    }
  }
}

override_data {
  target = data.terraform_remote_state.iam
  values = {
    outputs = {
      service_account_email = "monitor-test@goalcatcher-test.iam.gserviceaccount.com"
    }
  }
}

override_data {
  target = data.terraform_remote_state.firestore
  values = {
    outputs = {
      firestore_database_id = "monitor-test"
    }
  }
}

run "uses_dependency_outputs" {
  command = plan
}
