// TERRAMATE: GENERATED AUTOMATICALLY DO NOT EDIT

data "terraform_remote_state" "dependencies" {
  backend = "local"
  config = "local" == "local" ? {
    path = "${path.module}/../${each.key}/terraform.tfstate"
    } : {
    bucket = ""
    prefix = "goalcatcher/production/${each.key}"
  }
  for_each = toset([
    "project-services",
  ])
}
