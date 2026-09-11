variable "subnet_cidr" {
  description = "IPv4 CIDR assigned to the dedicated monitor subnet."
  type        = string
  default     = "10.42.0.0/24"

  validation {
    condition     = can(cidrnetmask(var.subnet_cidr))
    error_message = "subnet_cidr must be a valid IPv4 CIDR."
  }
}
