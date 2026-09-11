import pytest

from src.evaluator import evaluate
from tests.conftest import event, fixture


@pytest.mark.parametrize(
    ("events", "team", "state", "reason"),
    [
        ([], 1, "ALERT_PENDING", "no_first_goal_and_tie"),
        ([event(10, 2)], 1, "ALERT_PENDING", "no_first_goal"),
        ([event(10)], 1, "PASSED", "scored_and_not_tied"),
        ([event(10), event(31, 2)], 1, "ALERT_PENDING", "tie"),
        ([event(10), event(31, 2)], 2, "ALERT_PENDING", "tie"),
        ([event(32)], 1, "PASSED", "scored_and_not_tied"),
        ([event(33)], 1, "ALERT_PENDING", "no_first_goal_and_tie"),
        ([event(10), event(33, 2)], 1, "PASSED", "scored_and_not_tied"),
        ([event(10), event(31, 2), event(33)], 1, "ALERT_PENDING", "tie"),
        ([event(10), event(20, 2), event(30, 2)], 1, "PASSED", "scored_and_not_tied"),
        ([event(10, detail="Missed Penalty")], 1, "ALERT_PENDING", "no_first_goal_and_tie"),
        ([event(10, detail="Penalty")], 1, "PASSED", "scored_and_not_tied"),
        (
            [event(10, kind="Card", detail="Yellow Card")],
            1,
            "ALERT_PENDING",
            "no_first_goal_and_tie",
        ),
    ],
)
def test_threshold_outcomes(events, team, state, reason):
    decision = evaluate(fixture(events=events), team, 32)
    assert (decision.state, decision.reason) == (state, reason)


def test_no_early_pass_after_first_goal():
    assert evaluate(fixture(minute=20, events=[event(10)]), 1, 32).state == "PENDING"


def test_late_goal_preserves_threshold_score_in_alert():
    decision = evaluate(fixture(events=[event(33)]), 1, 32)
    assert decision.threshold_home_goals == 0
    assert decision.current_home_goals == 1


def test_fractional_threshold():
    assert evaluate(fixture(minute=32, events=[event(32)]), 1, 31.97).state == "ALERT_PENDING"
    assert evaluate(fixture(minute=31), 1, 31.97).state == "PENDING"


@pytest.mark.parametrize("status", ["1H", "HT"])
def test_added_time_does_not_cross_second_half_threshold(status):
    row = fixture(minute=45, status=status)
    row["fixture"]["status"]["extra"] = 10
    assert evaluate(row, 1, 53).state == "PENDING"


def test_added_time_goal_before_second_half_threshold():
    decision = evaluate(fixture(minute=54, status="2H", events=[event(45, extra=10)]), 1, 53)
    assert decision.state == "PASSED"


def test_added_time_goal_after_first_half_threshold():
    decision = evaluate(fixture(minute=45, events=[event(45, extra=1)]), 1, 45)
    assert decision.state == "ALERT_PENDING"


def test_stoppage_time_can_expire_window():
    row = fixture(minute=45)
    row["fixture"]["status"]["extra"] = 10
    assert evaluate(row, 1, 44).state == "MISSED_WINDOW"


@pytest.mark.parametrize("status", ["PST", "CANC", "ABD", "AWD", "WO"])
def test_skipped_matches(status):
    assert evaluate(fixture(status=status, minute=None), 1, 32).state == "SKIPPED"


@pytest.mark.parametrize("status", ["FT", "AET", "PEN"])
def test_no_stale_finished_alert(status):
    assert evaluate(fixture(status=status, minute=90), 1, 32).state == "MISSED_WINDOW"


@pytest.mark.parametrize("status", ["NS", "TBD", "SUSP", "INT", "UNKNOWN"])
def test_nonplaying_states_wait(status):
    assert evaluate(fixture(status=status), 1, 32).state == "PENDING"


def test_expired_window():
    assert evaluate(fixture(minute=38), 1, 32).state == "MISSED_WINDOW"


def test_missing_events_differ_from_valid_empty_events():
    row = fixture()
    del row["events"]
    assert evaluate(row, 1, 32).state == "PENDING"


@pytest.mark.parametrize("score", [(1, 0), (None, None), (-1, 0)])
def test_unreliable_score_does_not_evaluate(score):
    assert evaluate(fixture(score=score), 1, 32).state == "PENDING"


@pytest.mark.parametrize("event_team", [1, 2])
def test_own_goal_reconciled_against_scoreboard(event_team):
    row = fixture(events=[event(20, event_team, "Own Goal")], score=(1, 0))
    assert evaluate(row, 1, 32).state == "PASSED"
    assert evaluate(row, 2, 32).state == "ALERT_PENDING"


def test_ambiguous_own_goals_wait():
    row = fixture(events=[event(20, 1, "Own Goal"), event(33, 2, "Own Goal")])
    assert evaluate(row, 1, 32).reason == "goal_timeline_inconsistent_or_ambiguous"


def test_unknown_goal_detail_waits():
    assert evaluate(fixture(events=[event(20, detail="Unrecognized")]), 1, 32).state == "PENDING"


def test_out_of_order_events_find_first_goal():
    result = evaluate(fixture(events=[event(30), event(10)]), 1, 32)
    assert result.first_goal_minute == 10


@pytest.mark.parametrize(
    "row", [{}, {"fixture": {}}, fixture(events=[event(99)]), fixture(events=[event(20, 3)])]
)
def test_malformed_feed_is_not_an_alert(row):
    assert evaluate(row, 1, 32).state == "PENDING"
