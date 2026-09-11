# Generates backend.tf, providers.tf, and tm_locals.tf for every stack.
# Run `terramate generate` after adding/changing globals.

generate_hcl "backend.tf" {
  content {
    terraform {
      required_version = global.terraform_version

      tm_dynamic "backend" {
        labels = [global.tf_state_backend]
        # Path-derived prefix so nested stacks don't collide on their basename.
        attributes = global.tf_state_backend == "local" ? {
          path = "terraform.tfstate"
          } : {
          bucket = global.tf_state_bucket
          prefix = "${global.tf_state_prefix}/${tm_trimprefix(terramate.stack.path.absolute, "/infra/")}"
        }
      }
    }
  }
}

generate_file "providers.tf" {
  content = <<EOT
# tflint-ignore-file: terraform_unused_declarations
// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

locals {
  terraform-git-repo = "${global.git_repo}"
}

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "${global.google_provider_version}"
    }
  }
}

provider "google" {
  project = "${global.project_id}"
  region  = "${global.region}"
  zone    = "${global.zone}"

  default_labels = {
    environment = "${global.environment}"
    project     = "${global.project_name}"
    managed-by  = "terraform"
  }
}
EOT
}

# Environment-wide constants from globals, exposed to every stack as locals.
generate_file "tm_locals.tf" {
  content = <<EOT
# tflint-ignore-file: terraform_unused_declarations
// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

locals {
  environment     = "${global.environment}"
  project_id      = "${global.project_id}"
  region          = "${global.region}"
  zone            = "${global.zone}"
  name_prefix     = "${global.name_prefix}"
  enable_iap_ssh  = ${global.enable_iap_ssh}
  iap_ssh_members = ${tm_jsonencode(global.iap_ssh_members)}

  # For terraform_remote_state reads of sibling stacks
  tf_state_backend = "${global.tf_state_backend}"
  tf_state_bucket  = "${global.tf_state_bucket}"
  tf_state_prefix  = "${global.tf_state_prefix}"
}
EOT
}
