# tflint-ignore-file: terraform_unused_declarations
// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

locals {
  project_id      = "goalcatcher-508312"
  region          = "europe-central2"
  zone            = "europe-central2-a"
  name_prefix     = "football-goal-alert"
  enable_iap_ssh  = true
  iap_ssh_members = []

  # For terraform_remote_state reads of sibling stacks
  tf_state_backend = "gcs"
  tf_state_bucket  = "tf-state-goalcatcher"
}
