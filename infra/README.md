# Football goal alert infrastructure

Terramate orchestrates a separate Terraform stack for each GCP service in the
deployment described in `football_first_goal_architecture_proposal.md`. There is
a single environment, so the stacks sit directly under `infra/`:

```text
infra/
├── globals.tm.hcl                # environment constants: project, region, prefix…
├── generate.tm.hcl               # generates backend.tf / providers.tf / tm_locals.tf per stack
├── project-services/             # required Google Cloud APIs
│   ├── stack.tm.hcl
│   ├── main.tf
│   └── outputs.tf
├── networking/                   # VPC, subnet, and IAP firewall
│   ├── stack.tm.hcl
│   ├── main.tf
│   └── outputs.tf
├── iam/                          # VM service account and operator access
│   ├── stack.tm.hcl
│   ├── main.tf
│   └── outputs.tf
├── firestore/                    # durable monitor state
│   ├── stack.tm.hcl
│   ├── main.tf
│   └── outputs.tf
└── compute-engine/               # VM and systemd runtime
    ├── stack.tm.hcl
    ├── main.tf
    ├── data.tf                   # sibling stack outputs read from their state
    ├── outputs.tf
    ├── templates/
    └── tests/
```

Every directory containing a `stack.tm.hcl` is a stack with its own Terraform
state. The repository-root `terramate.tm.hcl` supplies the Terramate version
constraint and git safeguards. All stacks are tagged `production` and with their
service name, so `terramate run --tags iam` selects one service and
`--tags production` selects them all.

`project-services` runs first; `networking`, `iam`, and `firestore` depend on it.
`compute-engine` depends on those three stacks and reads their Terraform outputs
through the `terraform_remote_state` sources in its `data.tf`. The `after` and
`wants` entries in each `stack.tm.hcl` order execution and include prerequisite
stacks when a dependent service is selected.

Terramate generates three files into every stack from `generate.tm.hcl` — do not
hand-edit them:

| Generated file | Contents |
|---|---|
| `backend.tf` | `required_version` and the state backend, keyed by the stack's path |
| `providers.tf` | Google provider, `required_providers`, and `default_labels` |
| `tm_locals.tf` | environment globals exposed as Terraform `locals` |

Because every environment constant arrives as a local, the stacks declare no
Terraform variables and need no `.tfvars` files.

Together, the service stacks create:

- a dedicated VPC and subnet;
- an `e2-micro` Debian VM with Shielded VM features enabled;
- a Firestore Native database for team IDs, fixture watches, state transitions,
  and alert deduplication;
- a VM service account limited to Firestore access and writing logs and metrics;
- optional SSH access restricted to Google IAP, including the IAM permissions
  required by `gcloud compute ssh`; and
- the `football-goal-alert.service` systemd unit with `Restart=always` and
  `RestartSec=15`.

The VM has an ephemeral external address for inexpensive outbound API access.
No public application ingress rule is created. The only optional ingress is TCP
22 from Google's IAP range.

## Prerequisites

- Terraform `>= 1.9, < 2.0`
- Terramate `~> 0.17.0`
- Google Cloud CLI (`gcloud`)
- a GCP project with billing enabled
- credentials allowed to enable services and create Compute Engine, Firestore,
  IAM, and networking resources

Authenticate Terraform with Application Default Credentials:

```bash
gcloud auth application-default login
```

## Configure

All configuration lives in [`infra/globals.tm.hcl`](globals.tm.hcl). Set
`project_id` — it ships as `replace-me` — and adjust `region`, `zone`,
`name_prefix`, `enable_iap_ssh`, and `iap_ssh_members` (each entry prefixed with
`user:`, `group:`, or `serviceAccount:`) as needed. Re-run `terramate generate`
after every change.

Settings used by a single service are locals in that service's `main.tf`:
`subnet_cidr` in `networking`, `firestore_location` and `firestore_database_id`
in `firestore`, and `machine_type`, `boot_disk_size_gb`, `deletion_protection`,
and `labels` in `compute-engine`. Keep the Firestore location close to the VM
region.

The `firestore` stack creates the `(default)` database. If it already exists,
initialize the stacks and apply `project-services` first, then import it:

```bash
terraform -chdir=infra/firestore import \
  google_firestore_database.monitor \
  'projects/PROJECT_ID/databases/(default)'
```

Configuration intentionally excludes `API_FOOTBALL_KEY`, `TELEGRAM_BOT_TOKEN`,
and `TELEGRAM_CHAT_ID`; passing those through Terraform would store them in
state.

By default, every service stores state in its own `terraform.tfstate` and reads
dependency state from sibling folders. For GCS state, create a versioned bucket
outside these stacks and set `tf_state_backend = "gcs"` and `tf_state_bucket` in
`globals.tm.hcl`. Generation assigns a path-derived prefix per stack, such as
`goalcatcher/production/networking` and `goalcatcher/production/firestore`, and
updates all dependency readers to the same backend. If local state already
exists, regenerate and run `terramate run -- terraform init -migrate-state`
from `infra/` to migrate each service.

## Generate and validate

Run Terramate commands from `infra/`:

```bash
cd infra
terramate fmt
terramate generate
terraform fmt -recursive
terramate run -- terraform init
terramate run -- terraform validate
```

Commit the generated files and each Terraform dependency lock file. Terramate
checks for untracked/uncommitted files before running commands; commit reviewed
changes before deployment. For local validation while editing, its specific
`--disable-safeguards=git-untracked,git-uncommitted` option is available.

From the repository root, the VM's dependency-wiring test runs with mocked
Google resources and upstream outputs:

```bash
terraform -chdir=infra/compute-engine test
```

## Plan and apply

For the first deployment, prerequisites must be applied before a dependent
service can plan against their outputs. From `infra/`, review the execution
order and run an interactive apply in each stack:

```bash
terramate list --run-order
terramate run -- terraform apply
```

For later changes, review and apply a saved plan for the affected service, then
plan its dependents using the updated outputs. For example, from the repository
root:

```bash
terraform -chdir=infra/networking plan -out=networking.tfplan
terraform -chdir=infra/networking apply networking.tfplan
terraform -chdir=infra/compute-engine plan -out=compute-engine.tfplan
terraform -chdir=infra/compute-engine apply compute-engine.tfplan
```

VM deletion protection is enabled by default. Before an intentional destroy,
set `deletion_protection = false` in `compute-engine/main.tf` and apply that
change first. Firestore has API delete protection enabled and Terraform's
deletion policy is `ABANDON`, so a Firestore stack destroy removes it from
Terraform state without deleting monitor data. When intentionally destroying all
infrastructure, use Terramate's `--reverse` flag so dependents are removed
before their prerequisites.

## Deploy the application

Host initialization installs Python, creates the application user and directory
layout, and installs the systemd unit. It does not start the unit until
application code and a secret environment file exist.

Get the IAP connection command:

```bash
terraform -chdir=infra/compute-engine output -raw iap_ssh_command
```

Deploy `src/`, `config/`, `pyproject.toml`, and `uv.lock` to
`/opt/football-goal-alert`, then install the locked dependencies with
`uv sync --frozen --no-dev` from that directory. Then create this file directly
on the VM:

```dotenv
API_FOOTBALL_KEY=replace-me
TELEGRAM_BOT_TOKEN=replace-me
TELEGRAM_CHAT_ID=replace-me
```

Store it as `/opt/football-goal-alert/.env`, owned by `football-alert`, with mode
`0600`, then enable the service:

```bash
sudo systemctl enable --now football-goal-alert
sudo systemctl status football-goal-alert
journalctl -u football-goal-alert -f
```

The startup script supplies `GOOGLE_CLOUD_PROJECT` and `FIRESTORE_DATABASE_ID`
through `/etc/football-goal-alert.env`, using the Firestore stack output. The
service loads that file before the secret `.env`; the application must use those
values when creating its Firestore client.

Firestore holds all durable monitor state independently of the VM. Application
files and the virtual environment are replaceable deployment artifacts and must
be redeployed after VM replacement.
