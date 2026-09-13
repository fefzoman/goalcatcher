# Football goal alert infrastructure

Five Terramate stacks provision the single `production` environment on Google
Cloud. Every directory holding a `stack.tm.hcl` is a stack with its own Terraform
state; the repository-root `terramate.tm.hcl` pins the Terramate version and the
git safeguards.

```text
infra/
├── globals.tm.hcl        environment constants: project, region, prefix…
├── generate.tm.hcl       generates backend.tf / providers.tf / tm_locals.tf per stack
├── project-services/     required Google Cloud APIs
├── networking/           VPC, subnet, and IAP firewall
├── iam/                  VM service account, secrets, and operator access
├── firestore/            durable monitor state
└── compute-engine/       VM and systemd runtime (+ data.tf, templates/, tests/)
```

Each stack is tagged `production` plus its service name, so `terramate run --tags
iam` selects one service and `--tags production` selects them all.

`project-services` runs first; `networking`, `iam`, and `firestore` depend on it;
`compute-engine` depends on those three and reads their outputs through the
`terraform_remote_state` sources in its `data.tf`. The `after`/`wants` entries in
each `stack.tm.hcl` order execution and pull in prerequisites when a dependent
service is selected.

Terramate generates three files into every stack — do not hand-edit them, and
commit them along with each `.terraform.lock.hcl`:

| Generated file | Contents |
|---|---|
| `backend.tf` | `required_version` and the GCS backend, keyed by the stack's path |
| `providers.tf` | Google provider, `required_providers`, and `default_labels` |
| `tm_locals.tf` | environment globals exposed as Terraform `locals` |

Because every environment constant arrives as a local, the stacks declare no
Terraform variables and need no `.tfvars` files.

## What the stacks create

- a dedicated VPC and subnet;
- an `e2-micro` Debian VM with Shielded VM features enabled;
- a Firestore Native database for team IDs, fixture watches, state transitions
  and alert deduplication;
- three Secret Manager containers for the API-Football and Telegram credentials;
- a VM service account limited to Firestore, those three secrets, and writing
  logs and metrics;
- optional SSH restricted to Google IAP, including the IAM permissions
  `gcloud compute ssh` needs; and
- the `football-goal-alert.service` systemd unit (`Restart=always`,
  `RestartSec=15`).

The VM has an ephemeral external address for inexpensive outbound API access. No
public application ingress rule is created; the only optional ingress is TCP 22
from Google's IAP range.

## Prerequisites

Terraform `>= 1.9, < 2.0`, Terramate `~> 0.17.0`, `gcloud`, a GCP project with
billing enabled, and credentials allowed to enable services and create Compute
Engine, Firestore, IAM and networking resources.

```bash
gcloud auth application-default login
```

## Configure

All environment configuration lives in [`globals.tm.hcl`](globals.tm.hcl):
`project_id`, `region`, `zone`, `name_prefix`, `enable_iap_ssh`, and
`iap_ssh_members` (each entry prefixed `user:`, `group:`, or `serviceAccount:`).
Re-run `terramate generate` after every change.

Settings used by a single service are locals in that service's `main.tf`:
`subnet_cidr` (networking); `firestore_location`, `firestore_database_id`
(firestore); `machine_type`, `boot_disk_size_gb`, `deletion_protection`, `labels`
(compute-engine). Keep the Firestore location close to the VM region.

State is stored in GCS: `tf_state_backend = "gcs"` with bucket
`tf_state_bucket` (`tf-state-goalcatcher`). Create that versioned bucket outside
these stacks — nothing here provisions it. Generation assigns each stack a
path-derived prefix (`networking`, `firestore`, …) and points all dependency
readers at the same backend. Switching backends means regenerating and running
`terramate run -- terraform init -migrate-state` from `infra/`.

The `firestore` stack creates the `(default)` database. If it already exists,
apply `project-services` first, then import it:

```bash
terraform -chdir=infra/firestore import \
  google_firestore_database.monitor 'projects/PROJECT_ID/databases/(default)'
```

### Secrets

Terraform creates the three Secret Manager containers and grants the VM service
account access, but intentionally creates **no secret versions** — values passed
through Terraform would land in state. Add them out of band after applying
`project-services` and `iam`, entering one value per command and finishing with
Ctrl-D:

```bash
gcloud secrets versions add football-goal-alert-api-football-key \
  --project=goalcatcher-508312 --data-file=-
gcloud secrets versions add football-goal-alert-telegram-bot-token \
  --project=goalcatcher-508312 --data-file=-
gcloud secrets versions add football-goal-alert-telegram-chat-id \
  --project=goalcatcher-508312 --data-file=-
```

The application reads the latest enabled version at startup. To rotate, add a new
version and restart the service, then disable the previous version once verified.

### Google Sheets access

The project-services stack enables `sheets.googleapis.com` and the VM carries the
Sheets OAuth scope, but spreadsheet access is granted by **sharing the file**, not
by a project IAM role. Share
[the destination spreadsheet](https://docs.google.com/spreadsheets/d/1-gZnwabNdLarv8ofDXRDh0XOzKa6g4Ozyx202ZPKEx8/edit)
as Editor with the VM service account
(`football-goal-alert-vm@goalcatcher-508312.iam.gserviceaccount.com`, or
`terraform -chdir=infra/iam output -raw service_account_email`). The app uses the
attached service account; no local Google credentials are copied to the VM.
`GOOGLE_SHEETS_SPREADSHEET_ID` in `.env` overrides the default spreadsheet.

## CI

[`.github/workflows/`](../.github/workflows/) lints, plans, applies and deploys
this environment with no approval gates; the repository README documents the
workflows and the GitHub/Workload Identity setup they need.

Two infra-specific notes: change detection is Terramate's own, against
`origin/main` on a pull request and `HEAD^` on a push, so only touched stacks
run. The manual `workflow_dispatch` runs take an optional `tag` or `folder_path`
and skip change detection entirely — that is how you apply a first deployment,
when nothing is "changed" yet.

## Local workflow

```bash
cd infra
terramate fmt
terramate generate
terraform fmt -recursive
terramate run -- terraform init
terramate run -- terraform validate
```

Terramate checks for untracked/uncommitted files before running, so commit
reviewed changes first; while editing locally,
`--disable-safeguards=git-untracked,git-uncommitted` bypasses just those checks.

The VM's dependency-wiring test runs from the repository root with mocked Google
resources and upstream outputs (no credentials, no state access):

```bash
terraform -chdir=infra/compute-engine test
```

For a first deployment, prerequisites must be applied before dependents can plan
against their outputs:

```bash
terramate list --run-order
terramate run -- terraform apply
```

For later changes, apply a saved plan for the affected service, then plan its
dependents against the updated outputs:

```bash
terraform -chdir=infra/networking plan -out=networking.tfplan
terraform -chdir=infra/networking apply networking.tfplan
```

### Destroying

VM deletion protection is on by default: set `deletion_protection = false` in
`compute-engine/main.tf` and apply that change first. Firestore has API delete
protection enabled and a Terraform deletion policy of `ABANDON`, so destroying the
firestore stack drops it from state without deleting monitor data. Destroy
everything with Terramate's `--reverse` so dependents go before prerequisites.

## Deploying the application

Terraform only initializes the host — Python, uv, the `football-alert` user, the
venv, `/etc/football-goal-alert.env` and the unit file. Application files are a
separate, replaceable artifact, so **they must be redeployed after the VM is
replaced**; Firestore holds all durable state independently of the VM.

`deploy.yml` does this automatically. To deploy by hand, from the repository root
(VM `football-goal-alert`, project `goalcatcher-508312`, zone `europe-central2-a`
— adjust if `globals.tm.hcl` changes):

```bash
ARCHIVE=$(mktemp -t goalcatcher-deploy)
tar --exclude='__pycache__' -czf "$ARCHIVE" \
  src config pyproject.toml uv.lock deploy/football-goal-alert.service .env

gcloud compute scp "$ARCHIVE" football-goal-alert:~/deploy.tar.gz \
  --project=goalcatcher-508312 --zone=europe-central2-a --tunnel-through-iap

gcloud compute ssh football-goal-alert \
  --project=goalcatcher-508312 --zone=europe-central2-a --tunnel-through-iap
```

`terraform -chdir=infra/compute-engine output -raw iap_ssh_command` prints the
current connection command.

The packaged `.env` holds non-secret settings and the Secret Manager IDs only —
never `API_FOOTBALL_KEY`, `TELEGRAM_BOT_TOKEN` or `TELEGRAM_CHAT_ID`:

```dotenv
API_FOOTBALL_KEY_SECRET_ID=football-goal-alert-api-football-key
TELEGRAM_BOT_TOKEN_SECRET_ID=football-goal-alert-telegram-bot-token
TELEGRAM_CHAT_ID_SECRET_ID=football-goal-alert-telegram-chat-id
TIMEZONE=Europe/Kyiv
```

The startup script supplies `GOOGLE_CLOUD_PROJECT` and `FIRESTORE_DATABASE_ID`
through `/etc/football-goal-alert.env`. `.env` is the unit's *second*
`EnvironmentFile`, so anything it sets wins — if it also defines the project or
database, make sure they name the production resources. `deploy.yml` omits both
for exactly this reason.

Then on the VM:

```bash
sudo systemctl stop football-goal-alert
sudo tar -xzf ~/deploy.tar.gz -C /opt/football-goal-alert
sudo chown -R football-alert:football-alert /opt/football-goal-alert
sudo chmod 600 /opt/football-goal-alert/.env

cd /opt/football-goal-alert
sudo -H -u football-alert uv sync --frozen --no-dev
sudo install -m 0644 deploy/football-goal-alert.service \
  /etc/systemd/system/football-goal-alert.service
sudo -u football-alert .venv/bin/python -m src.monitor --check-config
```

Continue only if dependency installation and validation succeed. `--check-config`
validates team configuration offline; it checks neither live credentials,
Firestore permissions nor spreadsheet access. Verify Secret Manager access
separately — it needs the same environment the unit provides, and prints no
values:

```bash
sudo -u football-alert env $(cat /etc/football-goal-alert.env .env | grep -v '^#' | xargs) \
  .venv/bin/python -c 'from src.config import Settings; Settings.from_env(); print("Secrets accessible")'
```

Start and verify:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now football-goal-alert
sudo systemctl status football-goal-alert --no-pager
sudo journalctl -u football-goal-alert -n 100 --no-pager   # -f to follow
```

Expect `active (running)`, `enabled` and a `monitor_started` event. Successful
discovery and export log `discovery_complete` and `sheets_export_complete`.
Investigate repeated `monitor_error`, `football_backoff` or
`sheets_export_failed`.

Status checks, log reads and offline validation consume no football API requests;
starting or restarting the monitor begins normal API activity. **The monitor has
no hard enforcement of the 100-request daily limit.**
