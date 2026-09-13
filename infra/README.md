# Football goal alert infrastructure


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
- three Secret Manager containers for the API-Football and Telegram credentials;
- a VM service account limited to Firestore, its three secrets, and writing logs
  and metrics;
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

Terraform creates Secret Manager containers for `API_FOOTBALL_KEY`,
`TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID`, then grants the VM service account
access to those three containers. Terraform intentionally creates no secret
versions because values passed through Terraform would be stored in state. Add
each value out of band after applying `project-services` and `iam`:

```bash
gcloud secrets versions add football-goal-alert-api-football-key \
  --project=goalcatcher-508312 --data-file=-
gcloud secrets versions add football-goal-alert-telegram-bot-token \
  --project=goalcatcher-508312 --data-file=-
gcloud secrets versions add football-goal-alert-telegram-chat-id \
  --project=goalcatcher-508312 --data-file=-
```

Enter one value for each command and finish input with Ctrl-D. The application
reads the latest enabled version at startup. Add a new version and restart the
service to rotate a value; disable the previous version after the restart is
verified.

Google Sheets export uses the VM service account. The project-services stack
enables `sheets.googleapis.com`, and the VM includes the Sheets OAuth scope.
Share the destination spreadsheet with the email from
`terraform -chdir=infra/iam output -raw service_account_email` as an editor.
Spreadsheet access is granted by sharing the file, not a project IAM role.
The app defaults to spreadsheet `1-gZnwabNdLarv8ofDXRDh0XOzKa6g4Ozyx202ZPKEx8`,
tab `matches`; `GOOGLE_SHEETS_SPREADSHEET_ID` in `.env` can override it.

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

These steps assume Terraform has provisioned the VM and its startup script has
finished. Host initialization installs Python, uv, the application user and the
systemd unit, but does not start the application. The commands below use the
current configuration: VM `football-goal-alert`, project `goalcatcher-508312`,
zone `europe-central2-a`. Adjust them if `globals.tm.hcl` changes.

### 1. Package and upload from your Mac

Your local `.env` contains only non-secret settings and Secret Manager IDs:

```dotenv
GOOGLE_CLOUD_PROJECT=goalcatcher-508312
FIRESTORE_DATABASE_ID=(default)
API_FOOTBALL_KEY_SECRET_ID=football-goal-alert-api-football-key
TELEGRAM_BOT_TOKEN_SECRET_ID=football-goal-alert-telegram-bot-token
TELEGRAM_CHAT_ID_SECRET_ID=football-goal-alert-telegram-chat-id
TIMEZONE=Europe/Kyiv
```

Never place `API_FOOTBALL_KEY`, `TELEGRAM_BOT_TOKEN`, or `TELEGRAM_CHAT_ID` in
`.env`. The startup script supplies the project and database through
`/etc/football-goal-alert.env`; the Secret Manager IDs remain in `.env`. If `.env`
also defines the project or database, ensure they identify the production
resources; `.env` takes precedence in the systemd unit.

Package only the application files, unit and non-secret `.env`:

```bash
cd /Users/odobrynin/goalcatcher

DEPLOY_ARCHIVE=$(mktemp -t goalcatcher-deploy)
tar --exclude='__pycache__' -czf "$DEPLOY_ARCHIVE" \
  src config pyproject.toml uv.lock deploy/football-goal-alert.service .env

gcloud compute scp "$DEPLOY_ARCHIVE" \
  football-goal-alert:~/goalcatcher-deploy.tar.gz \
  --project=goalcatcher-508312 \
  --zone=europe-central2-a \
  --tunnel-through-iap
```

### 2. Connect to the VM

```bash
gcloud compute ssh football-goal-alert \
  --project=goalcatcher-508312 \
  --zone=europe-central2-a \
  --tunnel-through-iap
```

Alternatively, get the current connection command from the repository root with
`terraform -chdir=infra/compute-engine output -raw iap_ssh_command`.

### 3. Install and validate on the VM

Stop the service before replacing application files, then install the locked
runtime dependencies and the current systemd unit:

```bash
sudo systemctl stop football-goal-alert

sudo tar -xzf ~/goalcatcher-deploy.tar.gz \
  -C /opt/football-goal-alert

sudo chown -R football-alert:football-alert /opt/football-goal-alert
sudo chmod 600 /opt/football-goal-alert/.env

cd /opt/football-goal-alert
sudo -H -u football-alert uv sync --frozen --no-dev

sudo install -m 0644 deploy/football-goal-alert.service \
  /etc/systemd/system/football-goal-alert.service

sudo -u football-alert .venv/bin/python -m src.monitor --check-config
```

Continue only if dependency installation and configuration validation succeed.
`--check-config` validates team configuration offline; it does not check live
credentials, Firestore permissions or spreadsheet access. Verify Secret Manager
access separately without printing values:

```bash
sudo -u football-alert .venv/bin/python -c \
  'from src.config import Settings; Settings.from_env(); print("Secrets accessible")'
```

### 4. Grant spreadsheet access

Apply the current Terraform changes enabling `sheets.googleapis.com` and the VM's
Sheets OAuth scope before starting the exporter. Share
[the destination spreadsheet](https://docs.google.com/spreadsheets/d/1-gZnwabNdLarv8ofDXRDh0XOzKa6g4Ozyx202ZPKEx8/edit)
as **Editor** with the VM service account:

```text
football-goal-alert-vm@goalcatcher-508312.iam.gserviceaccount.com
```

If configuration changes, get the actual email from your local repository with
`terraform -chdir=infra/iam output -raw service_account_email`. The app uses this
attached service account on the VM and appends selected matches to the `matches`
tab. It does not require copying your local Google credentials to the VM.

### 5. Start and verify

On the VM:

```bash
sudo systemctl daemon-reload
sudo systemctl enable football-goal-alert
sudo systemctl restart football-goal-alert

sudo systemctl status football-goal-alert --no-pager
sudo systemctl is-enabled football-goal-alert
sudo journalctl -u football-goal-alert -n 100 --no-pager
```

Expect `active (running)`, `enabled`, and a `monitor_started` log event.
Successful discovery and export produce `discovery_complete` and
`sheets_export_complete`. Investigate repeated `monitor_error`,
`football_backoff` or `sheets_export_failed` events. To follow logs continuously:

```bash
sudo journalctl -u football-goal-alert -f
```

Status, log checks and offline configuration validation consume no football API
requests. Starting or restarting the monitor begins normal API activity.
**The monitor currently has no hard enforcement of the 100-request daily limit.**

Firestore holds all durable monitor state independently of the VM. Application
files and the virtual environment are replaceable deployment artifacts and must
be redeployed after VM replacement.
