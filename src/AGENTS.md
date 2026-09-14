# Runtime component instructions

## Responsibilities

- `config.py`: validates team configuration and non-secret environment settings,
  then reads the latest three credential versions from Secret Manager.
- `api_football.py`: owns API-Football HTTP behavior and safe error
  classification. It performs no hidden retries.
- `evaluator.py`: is the pure source of truth for threshold decisions. It has no
  network, persistence, logging, or delivery side effects.
- `monitor.py`: coordinates discovery, polling, evaluation, persistence,
  spreadsheet export, and delivery backoff.
- `store.py`: owns Firestore document shapes, transactions, revisions, claims,
  checkpoints, and pending spreadsheet payloads.
- `telegram.py`: formats alerts and classifies delivery outcomes conservatively.
- `sheets.py`: filters selected fixtures and atomically appends idempotent batches
  to the `matches` tab.

## Required working method

- Activate Goalcatcher with Serena, inspect a symbol with `find_symbol`, and use
  `find_referencing_symbols` before changing its contract. Read only the bodies
  needed for the change.
- Use Context7 before modifying Google Cloud clients, HTTPX behavior, dotenv,
  YAML parsing, or any external SDK contract.
- Keep I/O at adapters and orchestration boundaries. Add domain behavior to pure
  functions where possible.
- Never log exception text that may contain credentials, provider bodies, request
  URLs, Telegram URLs, or authorization headers.

## Behavioral constraints

- `evaluate()` remains deterministic and returns pending for malformed,
  incomplete, inconsistent, or ambiguous feeds.
- A goal at the threshold minute counts. Added time ordering and the five-minute
  lateness window must remain intact.
- Daily discovery must return only configured, enabled teams in their configured
  country and league. An empty enabled-team set makes no provider request.
- Persist state before acknowledging discovery/export completion. Telegram has no
  idempotency key, so retry only a proven-not-sent outcome.
- Spreadsheet alert names are exactly `NO GOAL`, `TIE`, or empty. `NO GOAL` wins
  when both rules or two selected-team outcomes apply.
- Direct plaintext credential environment variables are configuration errors.

## Verification

Run the test module corresponding to every changed runtime module. Run the full
offline suite when state transitions, API call counts, delivery, or shared data
shapes change. Assert request counts explicitly for quota-sensitive paths.
