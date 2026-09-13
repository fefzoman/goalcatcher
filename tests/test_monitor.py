from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from threading import Event

import pytest

from src.api_football import FootballError
from src.config import Team
from src.monitor import Monitor
from src.telegram import Delivery
from tests.conftest import API, Clock, Messenger, event, fixture, stored_watch


def test_single_delivery_across_polls_restart_and_rediscovery(settings, team, store):
    api, clock, messenger = API(), Clock(), Messenger()
    monitor = Monitor(settings, (team,), api, store, messenger, clock)
    monitor.tick()
    assert stored_watch(store)["state"] == "SENT"
    assert len(messenger.messages) == 1
    assert "Score at threshold: 0-0" in messenger.messages[0]
    for _ in range(3):
        clock.now += timedelta(seconds=30)
        monitor.tick()
    restarted = Monitor(settings, (team,), api, store, messenger, clock)
    restarted.tick()
    assert len(messenger.messages) == 1
    assert len(api.daily_calls) == 2
    assert len(api.live_calls) == 1
    assert len(store.watches.rows) == 1


def test_future_watch_does_not_poll_early(settings, team, store):
    api, clock = API(), Clock()
    clock.now -= timedelta(minutes=20)
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock)
    monitor.tick()
    assert api.live_calls == []
    assert stored_watch(store)["next_check_at"].isoformat() == "2026-09-11T12:31:30+00:00"


@pytest.mark.parametrize(
    ("minute", "status", "state", "event_calls"),
    [
        (31, "1H", "PENDING", []),
        (33, "1H", "SENT", [100]),
        (40, "1H", "MISSED_WINDOW", []),
        (90, "FT", "MISSED_WINDOW", []),
        (None, "PST", "SKIPPED", []),
    ],
)
def test_events_fetched_only_when_threshold_evaluation_needs_them(
    settings, team, store, minute, status, state, event_calls
):
    row = fixture(minute=minute, status=status)
    del row["events"]
    api = API([row])
    Monitor(settings, (team,), api, store, Messenger(), Clock()).tick()
    assert api.event_calls == event_calls
    assert stored_watch(store)["state"] == state


def test_first_goal_waits_for_possible_threshold_tie(settings, team, store):
    api, clock, sender = API([fixture(minute=31, events=[event(10)])]), Clock(), Messenger()
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    assert stored_watch(store)["state"] == "PENDING"
    api.rows = [fixture(events=[event(10), event(32, 2)])]
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert stored_watch(store)["state"] == "SENT"
    assert "TIE ALERT" in sender.messages[0]


def test_both_teams_share_live_request_but_have_separate_alerts(settings, team, store):
    other = Team("arsenal", "Arsenal", "England", "Premier League", 32)
    api, sender = API(), Messenger(Delivery("sent", 1), Delivery("sent", 2))
    Monitor(settings, (team, other), api, store, sender, Clock()).tick()
    assert api.live_calls == [[100]]
    assert len(sender.messages) == len(store.watches.rows) == 2


def test_goal_confirmation_can_change_initial_alert_to_pass(settings, team, store):
    settings = replace(settings, confirmation_seconds=60)
    api, clock, sender = API(), Clock(), Messenger()
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    assert stored_watch(store)["state"] == "PENDING"
    assert stored_watch(store)["reason"] == "awaiting_goal_confirmation"
    clock.now += timedelta(seconds=60)
    api.rows = [fixture(minute=34, events=[event(31)])]
    # A restart must retain the existing confirmation timestamp.
    Monitor(settings, (team,), api, store, sender, clock).tick()
    assert stored_watch(store)["state"] == "PASSED"
    assert sender.messages == []


def test_inconsistent_events_wait_for_complete_data(settings, team, store):
    api, clock, sender = API([fixture(score=(1, 0))]), Clock(), Messenger()
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    assert stored_watch(store)["state"] == "PENDING"
    api.rows = [fixture(events=[event(31)])]
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert stored_watch(store)["state"] == "PASSED"
    assert sender.messages == []


def test_missing_embedded_events_are_fetched(settings, team, store):
    row = fixture()
    del row["events"]
    sender = Messenger()
    Monitor(settings, (team,), API([row]), store, sender, Clock()).tick()
    assert len(sender.messages) == 1


def test_unknown_result_is_never_retried(settings, team, store):
    api, clock, sender = API(), Clock(), Messenger(Delivery("unknown"))
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    assert stored_watch(store)["state"] == "DELIVERY_UNKNOWN"
    clock.now += timedelta(minutes=3)
    Monitor(settings, (team,), api, store, sender, clock).tick()
    assert len(sender.messages) == 1


def test_proven_not_sent_retries_after_restart(settings, team, store):
    api, clock = API(), Clock()
    sender = Messenger(Delivery("retry", retry_after=45), Delivery("sent", 123))
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    assert stored_watch(store)["state"] == "ALERT_PENDING"
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert len(sender.messages) == 1
    clock.now += timedelta(seconds=15)
    Monitor(settings, (team,), api, store, sender, clock).tick()
    assert len(sender.messages) == 2
    assert stored_watch(store)["state"] == "SENT"
    assert stored_watch(store)["delivery_attempts"] == 2


def test_persistence_failure_after_send_does_not_resend(settings, team, store, monkeypatch):
    api, clock, sender = API(), Clock(), Messenger()
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    transition = store.transition

    def fail_after_send(watch, fields, now):
        if fields.get("state") == "SENT":
            raise RuntimeError("Firestore unavailable")
        return transition(watch, fields, now)

    monkeypatch.setattr(store, "transition", fail_after_send)
    with pytest.raises(RuntimeError):
        monitor.tick()
    assert stored_watch(store)["state"] == "ALERT_CLAIMED"
    monkeypatch.setattr(store, "transition", transition)
    clock.now += timedelta(seconds=settings.claim_timeout_seconds + 1)
    Monitor(settings, (team,), api, store, sender, clock).tick()
    assert stored_watch(store)["state"] == "DELIVERY_UNKNOWN"
    assert len(sender.messages) == 1


def test_concurrent_workers_cannot_claim_same_revision(settings, team, store):
    api, clock, sender = API(), Clock(), Messenger(Delivery("retry"), Delivery("sent", 12))
    one = Monitor(settings, (team,), api, store, sender, clock)
    one.tick()
    watch = deepcopy(stored_watch(store))
    clock.now += timedelta(seconds=30)
    two = Monitor(settings, (team,), api, store, sender, clock)
    one._deliver(watch, clock.now)
    two._deliver(watch, clock.now)
    assert stored_watch(store)["state"] == "SENT"
    assert len(sender.messages) == 2  # one rejection, one accepted message


@pytest.mark.parametrize("disabled", [True, False])
def test_disabled_or_removed_team_skips_pending_watch(settings, team, store, disabled):
    api, clock = API([fixture(minute=31)]), Clock()
    Monitor(settings, (team,), api, store, Messenger(), clock).tick()
    teams = (replace(team, enabled=False),) if disabled else ()
    Monitor(settings, teams, api, store, Messenger(), clock).tick()
    assert stored_watch(store)["state"] == "SKIPPED"


def test_configuration_change_does_not_reset_snapshot_threshold(settings, team, store):
    api, clock = API([fixture(minute=31)]), Clock()
    Monitor(settings, (team,), api, store, Messenger(), clock).tick()
    Monitor(settings, (replace(team, threshold=40),), api, store, Messenger(), clock).tick()
    assert stored_watch(store)["threshold"] == 32


def test_no_event_coverage_no_watch(settings, team, store):
    api = API()
    api.coverage = False
    Monitor(settings, (team,), api, store, Messenger(), Clock()).tick()
    assert store.watches.rows == {}
    assert api.live_calls == []


def test_cup_or_different_country_not_discovered(settings, team, store):
    rows = [fixture(), fixture(fixture_id=101)]
    rows[0]["league"]["name"] = "FA Cup"
    rows[1]["league"]["country"] = "World"
    Monitor(settings, (team,), API(rows), store, Messenger(), Clock()).tick()
    assert store.watches.rows == {}


def test_api_failure_backs_off_and_does_not_checkpoint_discovery(settings, team, store):
    api, clock = API(), Clock()
    api.error = FootballError("rate_limit", retry_after=180)
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock)
    monitor.tick()
    assert store.client.collection("runtime").rows == {}
    clock.now += timedelta(seconds=60)
    monitor.tick()
    assert len(api.daily_calls) == 1
    api.error = None
    clock.now += timedelta(seconds=120)
    monitor.tick()
    assert stored_watch(store)["state"] == "SENT"


def test_live_failure_preserves_pending_watch(settings, team, store):
    api, clock = API([fixture(minute=31)]), Clock()
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock)
    monitor.tick()
    api.error = FootballError("rate_limit", retry_after=120)
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert stored_watch(store)["state"] == "PENDING"
    calls = len(api.live_calls)
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert len(api.live_calls) == calls


def test_long_stale_fixture_expires_without_api_call(settings, team, store):
    clock = Clock()
    clock.now += timedelta(hours=8)
    api = API()
    Monitor(settings, (team,), api, store, Messenger(), clock).tick()
    assert stored_watch(store)["state"] == "MISSED_WINDOW"
    assert not api.live_calls


def test_kickoff_change_reschedules_watch(settings, team, store):
    api, clock = API([fixture(minute=31)]), Clock()
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock)
    monitor.tick()
    api.rows = [fixture(kickoff="2026-09-11T14:00:00+00:00", status="NS", minute=None)]
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert stored_watch(store)["kickoff_at"].hour == 14
    assert stored_watch(store)["next_check_at"].hour == 14
    assert stored_watch(store)["state"] == "PENDING"


def test_retry_cannot_send_after_deadline(settings, team, store):
    api, clock, sender = API(), Clock(), Messenger(Delivery("retry", retry_after=1000))
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    clock.now += timedelta(seconds=1000)
    monitor.tick()
    assert stored_watch(store)["state"] == "MISSED_WINDOW"
    assert len(sender.messages) == 1


def test_bounded_retry_attempts(settings, team, store):
    settings = replace(settings, max_delivery_attempts=1)
    api, clock, sender = API(), Clock(), Messenger(Delivery("retry"))
    monitor = Monitor(settings, (team,), api, store, sender, clock)
    monitor.tick()
    clock.now += timedelta(seconds=30)
    monitor.tick()
    assert stored_watch(store)["state"] == "FAILED"
    assert len(sender.messages) == 1


def test_discovery_runs_next_calendar_day(settings, team, store):
    api, clock = API([]), Clock()
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock)
    monitor.tick()
    clock.now = monitor.next_discovery
    monitor.tick()
    assert [day for day, _ in api.daily_calls] == ["2026-09-10", "2026-09-11", "2026-09-12"]


def test_run_stops_without_work_if_signalled(settings, team, store):
    api = API()
    stop = Event()
    stop.set()
    Monitor(settings, (team,), api, store, Messenger(), Clock()).run(stop)
    assert api.daily_calls == []
