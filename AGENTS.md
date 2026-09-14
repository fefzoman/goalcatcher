# Goalcatcher agent instructions

## Scope and precedence

This file applies to the whole repository. A nested `AGENTS.md` adds component
rules for its directory. Follow both; the nearest file wins if rules conflict.

## Project

Goalcatcher is a continuously running Python 3.11 service. It discovers domestic
league fixtures for configured teams, evaluates no-goal and tie rules at a team
threshold, sends Telegram alerts, stores durable state in Firestore, and appends
fixture snapshots to Google Sheets. Terraform and Terramate provision the Google
Cloud services, IAM, network, Firestore database, and Compute Engine VM.

The runtime flow is:

1. `src.config` loads teams, non-secret settings, and Secret Manager values.
2. `src.monitor` discovers selected fixtures and creates durable watches.
3. `src.api_football` performs bounded API-Football requests.
4. `src.evaluator` reconstructs the threshold score from goal events.
5. `src.store` commits watch and delivery state in Firestore transactions.
6. `src.telegram` formats and sends eligible alerts.
7. `src.sheets` appends idempotent batches to the `matches` tab.

## Mandatory MCP workflow

- At the start of every repository task, activate the current project with
  Serena and read its project instructions. Use Serena before opening source
  files when locating implementations, symbols, references, call paths, or
  related tests. Prefer its symbol-aware edit operations for structural changes.
- Before changing code or infrastructure that uses an external library, API,
  service, CLI, or Terraform provider, use Context7 first: resolve the library
  ID, query the current documentation for the exact behavior, and implement from
  that result. Do not guess a current interface from memory.
- Use `rg` first only for exact strings, configuration keys, error text, and
  regex searches. `code_index` may supplement Serena after the Serena pass.
- Do not silently bypass a required MCP call. If Serena or Context7 is unavailable,
  stop the affected work and report which server or tool failed.
- Every implementation report must state what Serena inspected and which
  Context7 documentation was consulted. For a task with no external dependency,
  explicitly state that the Context7 gate was not applicable.

## Repository invariants

- The API-Football plan permits 100 requests per day. Tests, lint, configuration
  checks, and diagnostics must not contact API-Football. Use mocks and block
  outbound sockets during the full test suite.
- Fetch daily fixtures with one date request, filter locally to enabled selected
  teams, and use singular `id` lookups because the free plan rejects `ids`.
- Never put `API_FOOTBALL_KEY`, `TELEGRAM_BOT_TOKEN`, or `TELEGRAM_CHAT_ID` in
  `.env`, process environment, Terraform input, state, logs, archives, or source.
  `.env` contains only non-secret settings and Secret Manager IDs.
- Preserve alert deduplication and conservative Telegram delivery. An ambiguous
  send result must never cause an automatic resend.
- Preserve Firestore discovery/export checkpoints and Google Sheets developer
  metadata; retries must not duplicate watches, messages, or spreadsheet batches.
- Treat incomplete or inconsistent match data as pending. Never infer an alert
  from data that cannot reproduce the score at the configured threshold.
- Preserve unrelated worktree changes. Do not rewrite history, delete lockfiles,
  or run destructive cleanup unless the user explicitly requests it.

## Component map

- `src/`: application runtime; see `src/AGENTS.md`.
- `config/`: monitored teams and thresholds; see `config/AGENTS.md`.
- `tests/`: offline test doubles and behavioral coverage; see `tests/AGENTS.md`.
- `infra/`: Terramate and Terraform stacks; see `infra/AGENTS.md`.
- `deploy/`: systemd service definition; see `deploy/AGENTS.md`.
- `outputs/`: generated local match artifacts; never treat them as runtime input
  unless the user explicitly selects a file.

## Verification

Use the smallest relevant check first, then the full offline suite for behavior
changes:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q tests
git diff --check
```

Load the project `.env` when configuration behavior is relevant, but keep tests
offline. For infrastructure, format and validate each affected stack and inspect
every plan for replacement or destruction before applying it.
