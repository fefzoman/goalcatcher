# Infrastructure component instructions

## Architecture

Infrastructure is split into independent Terramate stacks with separate state:

1. `project-services` enables required Google APIs.
2. `networking`, `iam`, and `firestore` depend on project services.
3. `compute-engine` depends on all three and reads their remote-state outputs.

`generate.tm.hcl` produces every `backend.tf`, `providers.tf`, and `tm_locals.tf`.
Edit the generator or globals, run `terramate generate`, and commit the generated
results. Do not hand-edit generated files.

## Required working method

- Activate the repository with Serena before tracing stack dependencies or
  editing HCL. Use Serena's Terraform symbols when available.
- Use Context7 for the exact installed HashiCorp Google provider resource schema
  and Terraform/Terramate behavior before changing a resource or lifecycle rule.
- Run `terraform fmt` and `terraform validate` in every affected stack. Run the
  compute dependency test when outputs or remote-state wiring change.
- Produce a saved plan and inspect the full action summary before apply. Stop if
  a plan replaces or destroys the VM, boot disk, Firestore database, Secret
  Manager secrets, or state unless the user explicitly requested that action.

## Invariants

- Resource names contain the application prefix and no environment segment.
- Terraform creates Secret Manager containers and IAM grants only. Never create
  secret versions or pass secret values through Terraform because values enter
  state.
- The VM service account gets per-secret accessor grants. Keep Sheets file access
  outside project IAM and retain the Sheets OAuth scope.
- Firestore delete protection and VM deletion protection remain enabled.
- Preserve stack order and state prefixes. Do not move a resource between stacks
  without an explicit state migration plan.
- Treat `metadata_startup_script` changes as replacement risks. Prefer changes
  that preserve the existing VM and disk.
