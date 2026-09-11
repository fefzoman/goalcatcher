variable "firestore_location" {
  description = "Firestore region. Keep this close to the VM region."
  type        = string
  default     = "europe-central2"
}

variable "firestore_database_id" {
  description = "Firestore database ID used by the monitor."
  type        = string
  default     = "(default)"

  validation {
    condition     = var.firestore_database_id == "(default)" || can(regex("^[a-z][a-z0-9-]{2,61}[a-z0-9]$", var.firestore_database_id))
    error_message = "firestore_database_id must be (default) or a valid 4-63 character database ID."
  }
}
