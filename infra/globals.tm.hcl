# Globals for the single production environment, inherited by every stack under
# infra/. Change values here rather than in a stack, then run
# `terramate generate`.
globals {

  # Google Cloud project that owns every resource. Replace before generating.
  project_id = "goalcatcher-508312"

  region = "europe-central2"
  zone   = "europe-central2-a"

  name_prefix = "football-goal-alert"

  # Administration reaches the VM only through Identity-Aware Proxy. Members
  # must be prefixed with user:, group:, or serviceAccount:.
  enable_iap_ssh  = true
  iap_ssh_members = []

  terraform_version       = ">= 1.9.0, < 2.0.0"
  google_provider_version = "~> 8.0"

  # Select one state backend for every stack and its dependency readers. For
  # GCS, set tf_state_backend = "gcs" and supply an existing versioned bucket.
  tf_state_backend = "gcs"
  tf_state_bucket  = "tf-state-goalcatcher"

}
