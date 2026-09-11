terramate {
  required_version = "~> 0.17.0"

  config {
    git {
      default_branch    = "main"
      check_untracked   = false
      check_uncommitted = false
      check_remote      = false
    }
  }
}
