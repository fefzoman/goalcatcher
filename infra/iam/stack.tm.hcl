stack {
  id          = "064e86b7-384e-4f78-a433-39a8bf4ba7d4"
  name        = "iam"
  description = "Monitor service account and operator IAM access"
  tags        = ["production", "iam"]
  after       = ["../project-services"]
  wants       = ["../project-services"]
}
