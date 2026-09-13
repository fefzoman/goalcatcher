# Goalcatcher

A continuously running Python service that watches selected football teams'
domestic league fixtures and sends Telegram alerts when, at a configured match
minute, the team has not scored **or the score is tied**. State lives in
Firestore Native; there is no SQLite or other local database.

## Alert rules

The threshold is inclusive: a goal at minute 32 counts for a threshold of 32. A
first goal does not end monitoring early — the opponent can still equalize before
the threshold. Both teams in a fixture are evaluated independently, but a 0–0 draw
produces one combined alert rather than two messages.

The evaluator reconstructs the score *at the threshold* from goal events rather
than reading the live score: waking at minute 34 with a 1–0 score whose goal came
at 33 still triggers a threshold-32 alert.

API-Football exposes minute-level, not second-accurate, goal times, so fractional
thresholds compare against recorded minutes, not an inferred goal second. Added
time is ordered within its half: 45+3 is after threshold 45 but before a
second-half threshold of 53. A 60-second confirmation delay lets the feed publish
or correct events, but cannot guarantee VAR finality. The goal timeline must
reconcile with the current scoreboard; missing, inconsistent or ambiguous data
never produces an alert or a successful evaluation.

## Repository

```text
src/
  monitor.py          daily discovery, polling, recovery and delivery claims
  api_football.py     API-Football v3 requests, batches and coverage checks
  telegram.py         plain-text alerts and delivery-outcome classification
  sheets.py           append selected-match snapshots to Google Sheets
  store.py            transactional Firestore persistence
  evaluator.py        pure match/threshold rules
  config.py           YAML and environment validation
config/teams.yaml     monitored teams, competitions, aliases and thresholds
deploy/               the football-goal-alert.service systemd unit
infra/                project-services, networking, iam, firestore, compute-engine
.github/workflows/    lint, plan, apply and application deploy
pyproject.toml        project metadata, dependencies and tool configuration
uv.lock               locked runtime and development dependencies
```

`.env` holds non-secret runtime configuration and is never committed. Every
infrastructure service keeps its own folder and `stack.tm.hcl` — see the
[infrastructure instructions](infra/README.md).

## Local setup

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+ and a Firestore Native
database.

```bash
uv sync --python 3.11
install -m 600 /dev/null .env
uv run python -m src.monitor --check-config
```

`uv sync` creates `.venv` from `uv.lock` with runtime and development
dependencies; `--no-dev` installs the runtime set alone.

Keep non-secret settings and the three Secret Manager IDs in `.env`. The
API-Football key, Telegram bot token and chat ID are loaded from the latest
enabled secret versions and must never be stored in `.env`. The bot must already
be allowed to post to that chat. Environment variables take precedence over
`.env`.

For local development against GCP, use Application Default Credentials with
permission to read the configured secrets:

```bash
gcloud auth application-default login
.venv/bin/python -m src.monitor
```

Starting the daemon reads and writes Firestore and **can send real Telegram
messages** — use a separate development database and test chat. `--check-config`
only validates the YAML offline; it checks neither credentials nor provider
coverage. SIGTERM and Ctrl+C finish the current bounded request, then stop.

## Team configuration

`config/teams.yaml` is the runtime source of truth. Changes take effect after
restarting the daemon. Existing watches retain their original threshold;
disabling or removing a team skips its unclaimed watches. Keep team keys stable —
they are part of alert identity.

Each entry requires `key`, `display_name`, `country`, `league` and `threshold`
(greater than 0, at most 90). Optional: `enabled`, `aliases`, `league_aliases`,
`api_team_id`, `api_league_id`.

Discovery matches names and aliases exactly after case, accent and punctuation
normalization, scoped to the configured country and competition. It learns team
IDs from matching fixtures rather than fuzzy searches and caches them in
Firestore. For production, verify provider names and IDs, and preferably pin the
IDs in YAML. The league must be a domestic `League` with goal-event coverage
enabled for that fixture's season; cups, friendlies and international
competitions are excluded. An unmatched alias or league produces no watch —
inspect discovery counts and the `team_ids` collection when commissioning.

`FootballAPI.daily_fixtures(date, timezone, teams=teams, cached_ids=cached_ids)`
requires the selected team configuration and returns only its enabled teams'
matching league fixtures. It filters one daily API response locally to conserve
quota; an empty or entirely disabled selection makes no request. Standalone
callers can use `load_teams("config/teams.yaml")` and omit `cached_ids`.

**West Ham remains configured for Championship, as supplied.** Verify that choice
before deployment; this implementation does not silently change competitions.

## Scheduling and recovery

- Daily discovery runs at startup and local midnight, fetching today plus any
  unfinished discovery for yesterday (fixtures spanning midnight). A durable
  date/config-fingerprint marker prevents repeated successful discovery on reboot.
- A watch wakes at kickoff + threshold − 30 seconds, then polls every 30 seconds.
  Halftime and delayed kickoffs use the provider's match clock, not elapsed wall
  time. Live refresh uses one `id` lookup per unique due fixture — the free plan
  rejects the bulk `ids` parameter — and both selected teams in one match share
  that lookup. A separate events request is made only when the response lacks
  events and threshold evaluation needs the goal timeline.
- Firestore active watches refresh at least every minute, including when there
  are no live API calls. API errors and quota limits back off exponentially;
  there are no hidden HTTP delivery retries.
- Evaluation stops five match minutes after the threshold. Finished matches, or
  watches older than six hours, become `MISSED_WINDOW`. Cancelled, postponed,
  abandoned and awarded fixtures are `SKIPPED`. A pending fixture whose kickoff
  changes is rescheduled; a previously skipped fixture is not reopened
  automatically if the provider reuses its ID.
- A fixture added after successful daily discovery is picked up only by a later
  day's discovery if its date is fetched. Intraday schedule refresh and manually
  reopening postponed fixtures are follow-up work, not guaranteed here.

Optional `.env` controls (defaults live in `Settings`): `POLL_SECONDS`,
`PRECHECK_SECONDS`, `CONFIRMATION_SECONDS`, `MAX_LATENESS_MINUTES`,
`MAX_FIXTURE_AGE_HOURS`, `REQUEST_TIMEOUT_SECONDS`, `CLAIM_TIMEOUT_SECONDS`,
`MAX_DELIVERY_ATTEMPTS`. `TIMEZONE` defaults to `Europe/Kyiv`.

## Google Sheets export

After each successful daily fixture fetch, the monitor queues the selected matches
for append to the `matches` tab of
[the configured spreadsheet](https://docs.google.com/spreadsheets/d/1-gZnwabNdLarv8ofDXRDh0XOzKa6g4Ozyx202ZPKEx8/edit).
Every fetch appends a new batch, including fixtures seen earlier, leaving existing
rows intact. A missing tab is created and an empty one receives headers; a
nonempty tab with different headers is left alone and logs
`sheets_header_conflict` rather than writing under the wrong columns.

Columns are fixture ID, local kickoff, timezone, country, league, round, home/away
teams, selected teams, thresholds, status, home/away goals and alert name. Alert
name is `NO GOAL` when a selected team had not scored by its threshold, `TIE` when
it had scored but the threshold score was tied, and empty when neither rule can be
confirmed. A 0–0 result is `NO GOAL`. Both selected teams in one fixture share a
row, with `NO GOAL` taking precedence if their results differ. These are snapshots
at discovery time, not a live scoreboard. Empty results append nothing. The
exporter upgrades the previous 13-column schema, leaving historical alert names
empty because those rows lack the goal timeline.

`GOOGLE_SHEETS_SPREADSHEET_ID` overrides the default spreadsheet; set it empty to
disable export. Credentials need the
`https://www.googleapis.com/auth/spreadsheets` scope and editor access to the
spreadsheet, and `sheets.googleapis.com` must be enabled in their project. On the
VM, share the spreadsheet with the attached service account; Terraform enables the
API and adds the Sheets scope.

Local ADC from an ordinary `gcloud auth application-default login` may lack the
Sheets scope. Follow [Google's local ADC instructions](https://cloud.google.com/docs/authentication/set-up-adc-local-dev-environment)
for non-Cloud scopes, using your OAuth client and retaining Cloud access:

```bash
gcloud auth application-default login --client-id-file=OAUTH_CLIENT_JSON \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets
```

Discovery completion and its pending export payload are saved together in
Firestore's `runtime` document. Sheets failures retry on a separate backoff
without repeating football API calls or blocking alert delivery. Each append and
its export marker commit in one Sheets batch, so a lost response or failed
acknowledgement retries without appending twice. Restart still skips completed
dates; without a new fetch it only retries pending exports.

To append previously downloaded fixtures, with no football API calls:

```bash
uv run python -m src.sheets --fixtures-json outputs/matches-2026-09-12/fixtures.json
```

This re-applies `config/teams.yaml` filters and appends a new batch on every
invocation. Use `--config` for a different team configuration.

## Firestore and duplicate prevention

Collections:

- `team_ids/{team_key}` — verified fixture team ID and configuration fingerprint.
- `watches/{fixture_id}--{team_key}` — immutable identity/threshold snapshot,
  kickoff, poll deadline, evaluation, revision and delivery attempt metadata.
- `runtime/discovery-{date}-{config_hash}` — successful discovery checkpoints
  and, when export is enabled, the pending Sheets payload and export status, so
  retries survive a restart.

Single-field state queries need no composite index. Production uses the VM's
attached service account through Application Default Credentials; do not deploy
service-account key files. State has no automatic retention policy yet — keep
watch documents, including terminal ones, to retain deduplication history.

```text
PENDING → PASSED / SKIPPED / MISSED_WINDOW / ALERT_PENDING
ALERT_PENDING → ALERT_CLAIMED → SENT
                             → ALERT_PENDING (proven not sent, bounded retry)
                             → FAILED (permanent rejection)
                             → DELIVERY_UNKNOWN (ambiguous result/stale claim)
```

Firestore transactions compare state and revision before claiming a delivery, so
only the winning worker sends. A network timeout after a possible send, a crash
while claimed, or a failed state write after success must not resend
automatically; stale claims become `DELIVERY_UNKNOWN` after 120 seconds.
Connection failures and explicit Telegram 429 rejections may retry within the
delivery deadline, permanent rejections may not.

This prioritizes avoiding duplicate automated alerts over guaranteed delivery:
Telegram `sendMessage` has no idempotency key, so exactly-once delivery is
impossible. Inspect unknown outcomes in Firestore and the chat manually, and do
not reset their state without accepting a possible duplicate.

## CI and deployment

[`.github/workflows/`](.github/workflows/) covers the single `production`
environment with no approval gates — a merge to `main` applies directly.

| Workflow | Trigger | Action |
|---|---|---|
| `terraform-lint.yml` | PR, push to `main` | generate-drift check, `terramate fmt`, `terraform fmt`/`tflint` on changed folders, `terraform test` |
| `plan.yml` | PR (infra paths), manual | plans changed stacks, posts a sticky PR comment |
| `apply.yml` | push to `main` (infra paths), manual | applies changed stacks |
| `deploy.yml` | push to `main` (app paths), manual | ships the application to the VM |

Authentication is keyless, via GCP Workload Identity Federation. Two repository
variables are required — `GCP_WORKLOAD_IDENTITY_PROVIDER` and
`GCP_DEPLOYER_SERVICE_ACCOUNT`; `TF_VERSION`, `TM_VERSION`, `GCP_PROJECT_ID`,
`GCE_INSTANCE`, `GCE_ZONE`, `NAME_PREFIX`, `TIMEZONE` and
`GOOGLE_SHEETS_SPREADSHEET_ID` are optional overrides of built-in defaults. No
secrets are needed. The deployer service account must also appear in
`iap_ssh_members` in [`infra/globals.tm.hcl`](infra/globals.tm.hcl) for
`deploy.yml` to reach the VM over IAP.

On a fresh environment, run `apply.yml` manually with empty inputs — it selects
all stacks by tag, whereas the push-triggered job only applies stacks changed
since `HEAD^`. Add the three Secret Manager values out of band before the first
deploy; the VM boots fine without them, but the service cannot start.

Terraform only initializes the host — Python, uv, the `football-alert` user, the
venv, `/etc/football-goal-alert.env` and the unit. Application files are separate,
replaceable artifacts and must be redeployed after the VM is replaced. The
canonical unit is `deploy/football-goal-alert.service`, embedded in the startup
script; on a running VM, install an updated copy to `/etc/systemd/system/`, reload
systemd and restart explicitly. Never upload your local `.venv` or ADC files.

The VM service account reads only the three application secrets, fetched at
startup and never written to either environment file. The service runs
unprivileged under systemd filesystem hardening; logs carry event names and
fixture/watch IDs, never API keys, Telegram URLs or response bodies.
[infra/README.md](infra/README.md) covers manual deployment and the full
Terramate workflow.

## Verification and dependencies

```bash
uv run ruff check .
uv run ruff format --check .
terraform -chdir=infra/compute-engine test   # mocked; no credentials or state
```

There is no Python test suite in the repository; `pyproject.toml` still declares
the pytest dev dependencies and a `testpaths` setting that currently match
nothing.

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`:

```bash
uv add httpx            # runtime dependency
uv add --dev pytest     # development dependency
uv lock --upgrade       # refresh every pin within the declared ranges
```

Live provider aliases and coverage, GCP IAM permissions, Firestore transaction
contention and Telegram connectivity are unverified by any offline check —
validate them against a development project and chat first.
Structured journal logs are the current observability interface; dashboards,
automated retention, schedule refresh and operational replay are not implemented.

Provider references: [API-Football fixture batches](https://www.api-football.com/news/post/how-to-get-all-fixtures-data-from-one-league),
[API-Football v3 guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide),
[Firestore transactions](https://docs.cloud.google.com/firestore/native/docs/manage-data/transactions),
and [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).
