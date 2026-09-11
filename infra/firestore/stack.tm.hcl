stack {
  id          = "f2b3c28c-3121-4d32-90a1-48f77a9bded6"
  name        = "firestore"
  description = "Durable Firestore state for fixture watches and alerts"
  tags        = ["production", "firestore"]
  after       = ["../project-services"]
  wants       = ["../project-services"]
}
