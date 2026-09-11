generate_hcl "_terramate_generated_variables.tf" {
  content {
    variable "project_id" {
      description = "Google Cloud project in which to deploy the monitor."
      type        = string

      validation {
        condition     = length(trimspace(var.project_id)) > 0
        error_message = "project_id must not be empty."
      }
    }

    variable "region" {
      description = "Google Cloud region for regional resources."
      type        = string
      default     = "europe-central2"
    }

    variable "zone" {
      description = "Google Cloud zone for the monitor VM."
      type        = string
      default     = "europe-central2-a"

      validation {
        condition     = startswith(var.zone, "${var.region}-")
        error_message = "zone must belong to region."
      }
    }

    variable "name_prefix" {
      description = "Prefix used for resource names."
      type        = string
      default     = "football-goal-alert"

      validation {
        condition = (
          length(var.name_prefix) >= 3 &&
          length(var.name_prefix) <= 27 &&
          can(regex("^[a-z]([-a-z0-9]*[a-z0-9])?$", var.name_prefix))
        )
        error_message = "name_prefix must be 3-27 lowercase letters, digits, or hyphens, start with a letter, and end with a letter or digit."
      }
    }

    variable "enable_iap_ssh" {
      description = "Allow SSH only through Google's Identity-Aware Proxy address range."
      type        = bool
      default     = true
    }
  }
}
