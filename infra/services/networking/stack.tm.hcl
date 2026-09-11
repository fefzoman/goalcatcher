stack {
  name        = "networking"
  description = "Monitor VPC, subnet, and IAP SSH firewall"
  id          = "2a1fd29a-a3e2-433b-9fd8-564a0d7c2db1"
  tags        = ["gcp", "production", "terraform", "networking"]
  after       = ["../project-services"]
  wants       = ["../project-services"]
}

globals {
  dependencies = ["project-services"]
}
