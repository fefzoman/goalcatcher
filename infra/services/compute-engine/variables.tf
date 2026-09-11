variable "machine_type" {
  description = "Compute Engine machine type used by the monitor."
  type        = string
  default     = "e2-micro"
}

variable "boot_disk_size_gb" {
  description = "Size of the VM boot disk. Durable monitor state is held in Firestore."
  type        = number
  default     = 10

  validation {
    condition     = var.boot_disk_size_gb >= 10
    error_message = "boot_disk_size_gb must be at least 10 GB."
  }
}

variable "deletion_protection" {
  description = "Protect the VM from accidental deletion. Set to false before an intentional destroy."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Additional labels applied to supported resources."
  type        = map(string)
  default     = {}
}
