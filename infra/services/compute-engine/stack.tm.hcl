stack {
  name        = "compute-engine"
  description = "Compute Engine VM and systemd monitor runtime"
  id          = "be28d258-a2ba-4e80-988d-e6e1d3398e47"
  tags        = ["gcp", "production", "terraform", "compute-engine"]
  after       = ["../networking", "../iam", "../firestore"]
  wants       = ["../networking", "../iam", "../firestore"]
  watch       = ["/deploy/football-goal-alert.service"]
}

globals {
  dependencies = ["networking", "iam", "firestore"]
}
