# Goalcatcher

A continuously running Python service that watches selected football teams'
domestic league fixtures and sends Telegram alerts when, at a configured match
minute, the team has not scored **or the score is tied**. State lives in
Firestore Native; there is no SQLite or other local database.

## Alert rules

The threshold is inclusive: a goal recorded at minute 32 counts for a threshold
of 32. A first goal does not finish monitoring early: the opponent can equalize
before the threshold. Both teams in a fixture are evaluated independently.

The evaluator reconstructs the score at the threshold from goal events. For
example, if it wakes at 34 with a 1–0 score but the goal was scored at 33, a
threshold of 32 still triggers an alert. It does not confuse the live score with
the threshold score. A 0–0 draw produces one combined alert, not two messages.

API-Football exposes minute-level, not second-accurate, goal times. Fractional
thresholds compare against those recorded minutes, not an inferred exact goal
second. Added time is ordered within its half: 45+3 is after threshold 45 but
before a second-half threshold of 53. A 60-second confirmation delay gives the
feed time to publish/correct events; it cannot guarantee VAR finality. The goal
timeline must reconcile with the current scoreboard. Missing, inconsistent or
ambiguous data never produces an alert or a successful evaluation.

## Repository

```text
src/
  monitor.py          daily discovery, polling, recovery and delivery claims
  api_football.py     API-Football v3 requests, batches and coverage checks
  telegram.py         plain-text alerts and delivery-outcome classification
  store.py            transactional Firestore persistence
  evaluator.py        pure match/threshold rules
  config.py           YAML and environment validation
config/teams.yaml     monitored teams, competitions, aliases and thresholds
deploy/football-goal-alert.service
tests/                offline evaluator, HTTP, persistence and daemon tests
infra/                project-services, networking, iam, firestore, compute-engine
pyproject.toml        project metadata, dependencies and tool configuration
uv.lock               locked runtime and development dependencies
.env.example          non-secret configuration template
```

Every infrastructure service keeps its own folder and `stack.tm.hcl`. See the
[infrastructure instructions](infra/README.md) and the
[architecture proposal](football_first_goal_architecture_proposal.md).

## Local setup

Requires [uv](https://docs.astral.sh/uv/), Python 3.11+ and a Firestore Native
database. From the repository root:

```bash
uv sync --python 3.11
cp .env.example .env
uv run python -m src.monitor --check-config
```

`uv sync` creates `.venv` from `uv.lock` with the runtime and development
dependencies; pass `--no-dev` for the runtime set alone.

Edit `.env` with your project, API-Football key, Telegram bot token and chat ID.
The bot must already be allowed to send messages to that chat. Keep `.env` out
of Git (`chmod 600 .env`). Environment variables take precedence over `.env`.

For local development against GCP, use Application Default Credentials:

```bash
gcloud auth application-default login
.venv/bin/python -m src.monitor
```

Starting the daemon reads/writes Firestore and **can send real Telegram messages**.
Use a separate development database and test chat. `--check-config` only validates
the YAML offline; it does not validate credentials or provider coverage. Tests
use fakes/mocked HTTP and require no credentials or external services. SIGTERM
and Ctrl+C finish the current bounded request and stop the loop.

## Team configuration

`config/teams.yaml` is the runtime source of truth. Its 28 initial thresholds and
league labels are copied unchanged from `thresholds.json`, which remains the
original reference. Changes take effect after restarting the daemon. Existing
watches retain their original threshold; disabling/removing a team skips its
unclaimed watches. Keep team keys stable because they are part of alert identity.

Each entry requires `key`, `display_name`, `country`, `league`, and `threshold`
(greater than 0 and at most 90). Optional fields are `enabled`, `aliases`,
`league_aliases`, `api_team_id`, and `api_league_id`.

Discovery matches names/aliases exactly after case/accent/punctuation
normalization, scoped to the configured country and competition. It learns team
IDs from matching fixtures, not fuzzy searches, and caches them in Firestore.
For production, verify provider names/IDs and preferably pin the IDs in YAML.
The league must be a domestic `League`, with goal-event coverage enabled for that
fixture's season. Cups, friendlies and international competitions are excluded.
An unmatched alias or league produces no watch; inspect discovery counts and
the `team_ids` collection when commissioning the service.

**West Ham remains configured for Championship, as supplied.** Verify that choice
before deployment; this implementation does not silently change competitions.

## Scheduling and recovery

- Daily discovery runs at startup and local midnight, fetching today and any
  unfinished discovery for yesterday (for fixtures spanning midnight). A durable
  date/config-fingerprint marker prevents repeated successful discovery on reboot.
- A watch wakes at kickoff + threshold − 30 seconds, then polls every 30 seconds.
  Halftime and delayed kickoffs are evaluated using the provider's match clock,
  not elapsed wall time. Live IDs are batched in groups of at most 20.
- Firestore active watches refresh at least every minute, including when there
  are no live API calls. API errors/quota limits back off exponentially; there
  are no hidden HTTP delivery retries.
- Evaluation stops five match minutes after the threshold. Finished matches or
  watches older than six hours become `MISSED_WINDOW`. Cancelled, postponed,
  abandoned and awarded fixtures are `SKIPPED`. A pending fixture whose kickoff
  changes is rescheduled; a previously skipped fixture is not automatically
  reopened if the provider reuses its ID.
- A new fixture added after successful daily discovery is picked up only by a
  later day's discovery if its date is fetched. Intraday schedule refresh and
  manually reopening postponed fixtures are follow-up work, not guaranteed here.

Optional `.env` controls (defaults are in `.env.example`): `POLL_SECONDS`,
`PRECHECK_SECONDS`, `CONFIRMATION_SECONDS`, `MAX_LATENESS_MINUTES`,
`MAX_FIXTURE_AGE_HOURS`, `REQUEST_TIMEOUT_SECONDS`, `CLAIM_TIMEOUT_SECONDS`, and
`MAX_DELIVERY_ATTEMPTS`. `TIMEZONE` defaults to `Europe/Kyiv`.

## Firestore and duplicate prevention

Collections:

- `team_ids/{team_key}`: verified fixture team ID and configuration fingerprint.
- `watches/{fixture_id}--{team_key}`: immutable identity/threshold snapshot,
  kickoff, poll deadline, evaluation, revision and delivery attempt metadata.
- `runtime/discovery-{date}-{config_hash}`: successful discovery checkpoints.

Single-field state queries need no composite index. Application Default
Credentials use the VM's attached service account in production; do not deploy
service-account key files. State has no automatic retention policy yet. Keep
watch documents (including terminal ones) to retain deduplication history.

```text
PENDING → PASSED / SKIPPED / MISSED_WINDOW / ALERT_PENDING
ALERT_PENDING → ALERT_CLAIMED → SENT
                             → ALERT_PENDING (proven not sent, bounded retry)
                             → FAILED (permanent rejection)
                             → DELIVERY_UNKNOWN (ambiguous result/stale claim)
```

Firestore transactions compare state and revision before claiming a delivery.
Only the winning worker sends. A network timeout after a possible send, a process
crash while claimed, or a failed state write after success must not automatically
resend the message. Stale claims become `DELIVERY_UNKNOWN` after 120 seconds.
Connection establishment failures and explicit Telegram 429 rejections can retry
within the delivery deadline; permanent rejections do not.

This prioritizes avoiding duplicate automated alerts over guaranteed delivery:
Telegram `sendMessage` has no idempotency key, so exactly-once delivery cannot be
guaranteed. Inspect unknown outcomes in Firestore and the chat manually. Do not
reset their state without accepting the possibility of a duplicate.

## Deploy on the provisioned VM

Provision the five Terramate service stacks following [infra/README.md](infra/README.md).
Host initialization creates `football-alert`, `uv`, a Python venv and the systemd
unit; it does not upload the application or start it. Copy `src/`, `config/`,
`pyproject.toml`, `uv.lock`, and a private `.env` to `/opt/football-goal-alert/`
through your approved deployment channel. Do not upload your local `.venv` or ADC
files. On the VM:

```bash
sudo chown -R football-alert:football-alert /opt/football-goal-alert
sudo chmod 600 /opt/football-goal-alert/.env
cd /opt/football-goal-alert
sudo -H -u football-alert uv sync --frozen --no-dev
sudo -H -u football-alert uv run --frozen --no-dev python -m src.monitor --check-config
sudo systemctl daemon-reload
sudo systemctl enable --now football-goal-alert
sudo systemctl status football-goal-alert
sudo journalctl -u football-goal-alert -f
```

The canonical unit is `deploy/football-goal-alert.service`; Terraform embeds it
in the VM startup script. For an already-running VM, install an updated unit
to `/etc/systemd/system/football-goal-alert.service`, reload systemd and restart
the service explicitly. Non-secret project/database values are also supplied by
`/etc/football-goal-alert.env`; `.env` may override them, so check your deployment.
The service runs unprivileged with systemd filesystem hardening. Logs contain
event names and fixture/watch IDs, never API keys, Telegram URLs or response bodies.

## Verification and dependencies

```bash
uv run pytest --cov=src --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
terraform -chdir=infra/compute-engine test
```

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. Change
them with:

```bash
uv add httpx            # runtime dependency
uv add --dev pytest     # development dependency
uv lock --upgrade       # refresh every pin within the declared ranges
```

Offline tests do not prove live provider aliases/coverage, GCP IAM permissions,
Firestore transaction contention in the real service, or Telegram connectivity.
Validate those with your development project/chat before production. Structured
journal logs are the current observability interface; dashboards, automated
retention, schedule refresh and operational replay tools are not implemented.

Provider references: [API-Football fixture batches](https://www.api-football.com/news/post/how-to-get-all-fixtures-data-from-one-league),
[API-Football v3 guide](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide),
[Firestore transactions](https://docs.cloud.google.com/firestore/native/docs/manage-data/transactions),
and [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).
