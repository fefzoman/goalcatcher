variable "iap_ssh_members" {
  description = "IAM members granted the roles needed for IAP tunnelling and OS Login administrator access, for example user:operator@example.com."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for member in var.iap_ssh_members : can(regex("^(user|group|serviceAccount):.+$", member))
    ])
    error_message = "Each iap_ssh_members entry must be an IAM member prefixed with user:, group:, or serviceAccount:."
  }
}
