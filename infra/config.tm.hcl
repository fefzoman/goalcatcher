globals {
  terraform_version       = ">= 1.9.0, < 2.0.0"
  google_provider_version = "~> 8.0"

  # Select one state backend for every service and its dependency readers.
  # For GCS, set state_backend = "gcs" and supply an existing bucket.
  state_backend = "local"
  state_bucket  = ""
  state_prefix  = "goalcatcher/production"
}

assert {
  assertion = tm_contains(["local", "gcs"], global.state_backend)
  message   = "state_backend must be local or gcs."
}

assert {
  assertion = global.state_backend != "gcs" || global.state_bucket != ""
  message   = "state_bucket must be set when using the GCS backend."
}

generate_hcl "_terramate_generated_versions.tf" {
  content {
    terraform {
      required_version = global.terraform_version

      required_providers {
        google = {
          source  = "hashicorp/google"
          version = global.google_provider_version
        }
      }
    }
  }
}

generate_hcl "_terramate_generated_provider.tf" {
  content {
    provider "google" {
      project = var.project_id
      region  = var.region
      zone    = var.zone
    }
  }
}

generate_hcl "_terramate_generated_backend.tf" {
  content {
    terraform {
      tm_dynamic "backend" {
        labels = [global.state_backend]
        attributes = global.state_backend == "local" ? {
          path = "terraform.tfstate"
          } : {
          bucket = global.state_bucket
          prefix = "${global.state_prefix}/${terramate.stack.name}"
        }
      }
    }
  }
}

generate_hcl "_terramate_generated_dependencies.tf" {
  condition = tm_length(global.dependencies) > 0

  content {
    data "terraform_remote_state" "dependencies" {
      for_each = toset(global.dependencies)
      backend  = global.state_backend
      config = global.state_backend == "local" ? {
        path = "${path.module}/../${each.key}/terraform.tfstate"
        } : {
        bucket = global.state_bucket
        prefix = "${global.state_prefix}/${each.key}"
      }
    }
  }
}
