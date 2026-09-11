"""Continuous daemon: daily discovery, deadline-based polling, durable delivery."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.api_football import FootballAPI, FootballError
from src.config import Settings, Team, fingerprint, load_teams
from src.evaluator import evaluate
from src.store import FirestoreStore
from src.telegram import Telegram, format_alert

LOG = logging.getLogger("goalcatcher")


def utcnow() -> datetime:
    return datetime.now(UTC)


def log(event: str, **fields) -> None:
    LOG.info(json.dumps({"event": event, **fields}, default=str, sort_keys=True))


def parse_kickoff(fixture: dict) -> datetime:
    result = datetime.fromisoformat(fixture["fixture"]["date"].replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Fixture kickoff requires an offset")
    return result.astimezone(UTC)


class Monitor:
    def __init__(
        self, settings: Settings, teams: tuple[Team, ...], api, store, telegram, clock=utcnow
    ):
        self.settings, self.teams = settings, teams
        self.api, self.store, self.telegram = api, store, telegram
        self.clock = clock
        self.config_hash = fingerprint(teams)
        self.next_discovery = datetime.min.replace(tzinfo=UTC)
        self.api_not_before = datetime.min.replace(tzinfo=UTC)
        self.api_failures = 0

    def _api_failure(self, exc: FootballError, now: datetime) -> None:
        self.api_failures += 1
        delay = max(exc.retry_after, min(30 * 2 ** min(self.api_failures, 7), 3600))
        self.api_not_before = now + timedelta(seconds=delay)
        log("football_backoff", reason=str(exc), retry_at=self.api_not_before)

    def discover(self, now: datetime) -> None:
        timezone = ZoneInfo(self.settings.timezone)
        local_day = now.astimezone(timezone).date()
        cached_ids = self.store.team_ids(self.config_hash)
        self.api.refresh_leagues()
        # Yesterday also covers a first deployment/restart across local midnight.
        for day in (local_day - timedelta(days=1), local_day):
            key = f"discovery-{day.isoformat()}-{self.config_hash}"
            if self.store.discovery_done(key):
                continue
            fixtures = self.api.daily_fixtures(day.isoformat(), self.settings.timezone)
            created = 0
            for fixture in fixtures:
                for side in ("home", "away"):
                    candidates = [
                        team
                        for team in self.teams
                        if team.enabled
                        and team.matches(fixture, fixture["teams"][side], cached_ids.get(team.key))
                    ]
                    if len(candidates) > 1:
                        raise ValueError("Multiple configured teams match the same fixture side")
                    if not candidates:
                        continue
                    team = candidates[0]
                    if not self.api.has_goal_coverage(fixture):
                        log(
                            "fixture_unsupported",
                            fixture_id=fixture["fixture"]["id"],
                            team=team.key,
                        )
                        continue
                    api_id = fixture["teams"][side]["id"]
                    if team.key in cached_ids and cached_ids[team.key] != api_id:
                        raise ValueError("Ambiguous team identity")
                    self.store.save_team_id(team.key, api_id, self.config_hash, now)
                    cached_ids[team.key] = api_id
                    kickoff = parse_kickoff(fixture)
                    precheck = kickoff + timedelta(
                        minutes=team.threshold,
                        seconds=-self.settings.precheck_seconds,
                    )
                    watch = {
                        "id": f"{fixture['fixture']['id']}--{team.key}",
                        "fixture_id": fixture["fixture"]["id"],
                        "team_key": team.key,
                        "api_team_id": api_id,
                        "team_name": team.display_name,
                        "league_name": fixture["league"]["name"],
                        "home_id": fixture["teams"]["home"]["id"],
                        "away_id": fixture["teams"]["away"]["id"],
                        "home_name": fixture["teams"]["home"]["name"],
                        "away_name": fixture["teams"]["away"]["name"],
                        "kickoff_at": kickoff,
                        "threshold": team.threshold,
                        "state": "PENDING",
                        "next_check_at": max(now, precheck),
                        "created_at": now,
                        "updated_at": now,
                        "delivery_attempts": 0,
                    }
                    created += int(self.store.create_watch(watch))
            # A partial failure leaves this absent: retrying creates no duplicates.
            self.store.finish_discovery(key, now)
            log("discovery_complete", date=day, fixtures=len(fixtures), watches_created=created)
        tomorrow = local_day + timedelta(days=1)
        self.next_discovery = datetime.combine(tomorrow, time.min, timezone).astimezone(UTC)

    def _update_fixture(self, watch: dict, fixture: dict, now: datetime) -> None:
        kickoff = parse_kickoff(fixture)
        # Detect a moved kickoff before making any threshold decision.
        if kickoff != watch["kickoff_at"]:
            self.store.transition(
                watch,
                {
                    "kickoff_at": kickoff,
                    "threshold_seen_at": None,
                    "next_check_at": max(
                        now + timedelta(seconds=self.settings.poll_seconds),
                        kickoff
                        + timedelta(
                            minutes=watch["threshold"],
                            seconds=-self.settings.precheck_seconds,
                        ),
                    ),
                },
                now,
            )
            return
        if (
            fixture["teams"]["home"]["id"] != watch["home_id"]
            or fixture["teams"]["away"]["id"] != watch["away_id"]
        ):
            log("fixture_identity_mismatch", watch_id=watch["id"])
            return
        decision = evaluate(
            fixture, watch["api_team_id"], watch["threshold"], self.settings.max_lateness_minutes
        )
        fields = {
            **decision.fields(),
            "last_status": fixture["fixture"]["status"]["short"],
            "next_check_at": now + timedelta(seconds=self.settings.poll_seconds),
        }
        if decision.state in {"PASSED", "ALERT_PENDING"}:
            seen = watch.get("threshold_seen_at")
            if seen is None:
                seen = now
                fields["threshold_seen_at"] = now
            if now < seen + timedelta(seconds=self.settings.confirmation_seconds):
                fields.update(state="PENDING", reason="awaiting_goal_confirmation")
            elif decision.state == "ALERT_PENDING":
                fields["delivery_deadline"] = now + timedelta(
                    minutes=max(
                        0,
                        watch["threshold"]
                        + self.settings.max_lateness_minutes
                        - decision.match_minute,
                    )
                )
                fields["next_check_at"] = now
        updated = self.store.transition(watch, fields, now)
        if updated and updated["state"] != "PENDING":
            log(
                "watch_evaluated",
                watch_id=watch["id"],
                state=updated["state"],
                reason=updated["reason"],
            )
            if updated["state"] == "ALERT_PENDING":
                self._deliver(updated, self.clock())

    def _deliver(self, watch: dict, now: datetime) -> None:
        if now > watch["delivery_deadline"]:
            self.store.transition(
                watch, {"state": "MISSED_WINDOW", "reason": "delivery_window_expired"}, now
            )
            return
        if watch["delivery_attempts"] >= self.settings.max_delivery_attempts:
            self.store.transition(
                watch, {"state": "FAILED", "reason": "delivery_retries_exhausted"}, now
            )
            return
        # Format before claiming; no side effects occur inside a transaction.
        message = format_alert(watch)
        claimed = self.store.transition(
            watch,
            {
                "state": "ALERT_CLAIMED",
                "delivery_attempt_id": str(uuid.uuid4()),
                "claimed_at": now,
                "delivery_attempts": watch["delivery_attempts"] + 1,
            },
            now,
        )
        if claimed is None:
            return
        if self.clock() >= now + timedelta(seconds=self.settings.claim_timeout_seconds):
            self.store.transition(
                claimed,
                {"state": "DELIVERY_UNKNOWN", "reason": "claim_expired_before_send"},
                self.clock(),
            )
            return
        result = self.telegram.send(message)
        finished_at = self.clock()
        fields = {
            "state": {
                "sent": "SENT",
                "retry": "ALERT_PENDING",
                "failed": "FAILED",
                "unknown": "DELIVERY_UNKNOWN",
            }[result.outcome],
            "delivery_result": result.reason,
        }
        if result.outcome == "sent":
            fields.update(alerted_at=finished_at, telegram_message_id=result.message_id)
        elif result.outcome == "retry":
            delay = max(result.retry_after, min(30 * 2 ** (claimed["delivery_attempts"] - 1), 3600))
            fields["next_check_at"] = finished_at + timedelta(seconds=delay)
        # If this write fails, leave the claim intact. NEVER re-send to compensate.
        updated = self.store.transition(claimed, fields, finished_at)
        log(
            "delivery_result",
            watch_id=watch["id"],
            outcome=result.outcome,
            persisted=updated is not None,
        )

    def tick(self) -> datetime:
        now = self.clock()
        if now >= max(self.next_discovery, self.api_not_before):
            try:
                self.discover(now)
                self.api_failures = 0
            except FootballError as exc:
                self._api_failure(exc, self.clock())
            except (KeyError, ValueError, TypeError, AttributeError):
                log("discovery_invalid_data")
                self.next_discovery = now + timedelta(minutes=5)
        now = self.clock()
        watches = self.store.active_watches()
        enabled = {team.key for team in self.teams if team.enabled}
        due = []
        wakeups = [now + timedelta(seconds=60), max(self.next_discovery, self.api_not_before)]
        for watch in watches:
            if watch["state"] == "ALERT_CLAIMED":
                expires = watch["claimed_at"] + timedelta(
                    seconds=self.settings.claim_timeout_seconds
                )
                if now >= expires:
                    self.store.transition(
                        watch, {"state": "DELIVERY_UNKNOWN", "reason": "stale_delivery_claim"}, now
                    )
                    log("stale_claim", watch_id=watch["id"])
                else:
                    wakeups.append(expires)
                continue
            if watch["team_key"] not in enabled:
                self.store.transition(
                    watch, {"state": "SKIPPED", "reason": "team_disabled_or_removed"}, now
                )
                continue
            if watch["state"] == "PENDING" and now > watch["kickoff_at"] + timedelta(
                hours=self.settings.max_fixture_age_hours
            ):
                self.store.transition(
                    watch, {"state": "MISSED_WINDOW", "reason": "fixture_expired"}, now
                )
                continue
            due_at = watch["next_check_at"]
            if watch["state"] == "PENDING":
                due_at = max(due_at, self.api_not_before)
            if now < due_at:
                wakeups.append(due_at)
            elif watch["state"] == "ALERT_PENDING":
                self._deliver(watch, now)
                wakeups.append(now + timedelta(seconds=self.settings.poll_seconds))
            else:
                due.append(watch)
        ids = list(dict.fromkeys(watch["fixture_id"] for watch in due))
        for start in range(0, len(ids), 20):
            batch = ids[start : start + 20]
            try:
                rows = self.api.fixtures(batch)
                fixtures = {row["fixture"]["id"]: row for row in rows}
                for fixture_id in batch:
                    fixture = fixtures.get(fixture_id)
                    if fixture is None:
                        log("fixture_missing", fixture_id=fixture_id)
                        continue
                    if "events" not in fixture or fixture["events"] is None:
                        fixture["events"] = self.api.events(fixture_id)
                    for watch in due:
                        if watch["fixture_id"] == fixture_id:
                            self._update_fixture(watch, fixture, self.clock())
                self.api_failures = 0
            except FootballError as exc:
                self._api_failure(exc, self.clock())
                break
            except (KeyError, ValueError, TypeError, AttributeError):
                log("live_fixture_invalid_data", fixture_ids=batch)
        if due:
            wakeups.append(self.clock() + timedelta(seconds=self.settings.poll_seconds))
        return max(self.clock() + timedelta(seconds=1), min(wakeups))

    def run(self, stop: threading.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                wake_at = self.tick()
                failures = 0
                delay = max(1, (wake_at - self.clock()).total_seconds())
            except Exception as exc:
                # Exception messages may contain credential-bearing request URLs.
                failures += 1
                log("monitor_error", error_type=type(exc).__name__)
                delay = min(15 * 2 ** min(failures, 5), 300)
            stop.wait(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="Football first-goal/tie Telegram monitor")
    parser.add_argument("--config", default="config/teams.yaml")
    parser.add_argument(
        "--check-config", action="store_true", help="Validate teams offline; no API calls or writes"
    )
    args = parser.parse_args()
    load_dotenv(override=False)
    try:
        teams = load_teams(args.config)
        if args.check_config:
            print(f"Configuration valid: {sum(team.enabled for team in teams)} enabled teams")
            return
        settings = Settings.from_env()
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # HTTPX's INFO request log includes the Telegram token in its URL.
    for name in ("httpx", "httpcore", "google", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    api = FootballAPI(settings.api_key, settings.request_timeout_seconds)
    telegram = Telegram(
        settings.telegram_token, settings.telegram_chat_id, settings.request_timeout_seconds
    )
    store = FirestoreStore(settings.project_id, settings.database_id)
    log("monitor_started", teams=sum(team.enabled for team in teams), timezone=settings.timezone)
    try:
        Monitor(settings, teams, api, store, telegram).run(stop)
    finally:
        api.close()
        telegram.close()
        store.close()
        log("monitor_stopped")


if __name__ == "__main__":
    main()
