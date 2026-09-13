# Outputs of sibling stacks that always apply before compute-engine (see
# stack.tm.hcl), read directly from their state instead of being passed as
# variables.
data "terraform_remote_state" "networking" {
  backend = local.tf_state_backend
  config = local.tf_state_backend == "local" ? {
    path = "${path.module}/../networking/terraform.tfstate"
    } : {
    bucket = local.tf_state_bucket
    prefix = "networking"
  }
}

data "terraform_remote_state" "iam" {
  backend = local.tf_state_backend
  config = local.tf_state_backend == "local" ? {
    path = "${path.module}/../iam/terraform.tfstate"
    } : {
    bucket = local.tf_state_bucket
    prefix = "iam"
  }
}

data "terraform_remote_state" "firestore" {
  backend = local.tf_state_backend
  config = local.tf_state_backend == "local" ? {
    path = "${path.module}/../firestore/terraform.tfstate"
    } : {
    bucket = local.tf_state_bucket
    prefix = "firestore"
  }
}
