stack {
  name        = "iam"
  description = "Monitor service account and operator IAM access"
  id          = "064e86b7-384e-4f78-a433-39a8bf4ba7d4"
  tags        = ["gcp", "production", "terraform", "iam"]
  after       = ["../project-services"]
  wants       = ["../project-services"]
}

globals {
  dependencies = ["project-services"]
}
