"""Pure threshold evaluation; never sends messages or writes persistent state."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Decision:
    state: str
    reason: str
    first_goal_minute: int | None = None
    threshold_home_goals: int | None = None
    threshold_away_goals: int | None = None
    match_minute: int | None = None
    current_home_goals: int | None = None
    current_away_goals: int | None = None

    def fields(self) -> dict:
        return asdict(self)


def threshold_reached(status: str, minute: int | None, threshold: float) -> bool:
    if type(minute) is not int:
        return False
    # 45+10 is still the first half, not minute 55 of the second half.
    if status in {"1H", "HT"} and threshold > 45:
        return False
    return minute >= threshold and status in {"1H", "HT", "2H", "ET", "BT", "P", "FT", "AET", "PEN"}


def evaluate(fixture: dict, team_id: int, threshold: float, max_lateness: int = 5) -> Decision:
    try:
        return _evaluate(fixture, team_id, threshold, max_lateness)
    except (KeyError, TypeError, ValueError, AttributeError):
        return Decision("PENDING", "incomplete_match_data")


def _evaluate(fixture: dict, team_id: int, threshold: float, max_lateness: int) -> Decision:
    status = fixture["fixture"]["status"]["short"]
    minute = fixture["fixture"]["status"]["elapsed"]
    if status in {"PST", "CANC", "ABD", "AWD", "WO"}:
        return Decision("SKIPPED", f"fixture_{status.lower()}")
    if status in {"FT", "AET", "PEN"}:
        return Decision("MISSED_WINDOW", "fixture_finished")
    if not threshold_reached(status, minute, threshold):
        return Decision("PENDING", "before_threshold")
    extra = fixture["fixture"]["status"].get("extra") or 0
    lateness = minute - threshold
    if minute == 45 and threshold <= 45:
        lateness += extra
    if lateness > max_lateness:
        return Decision("MISSED_WINDOW", "threshold_window_expired")

    home_id, away_id = fixture["teams"]["home"]["id"], fixture["teams"]["away"]["id"]
    if team_id not in (home_id, away_id) or home_id == away_id:
        raise ValueError
    current = (fixture["goals"]["home"], fixture["goals"]["away"])
    if any(type(value) is not int or value < 0 for value in current):
        raise ValueError
    events = fixture["events"]
    if not isinstance(events, list):
        raise ValueError
    goals = []
    for event in events:
        if event["type"] != "Goal" or event.get("detail") == "Missed Penalty":
            continue
        detail = event["detail"]
        if detail not in {"Normal Goal", "Penalty", "Own Goal"}:
            return Decision("PENDING", "unrecognized_goal_event")
        elapsed = event["time"]["elapsed"]
        added = event["time"].get("extra") or 0
        scorer = event["team"]["id"]
        if (
            type(elapsed) is not int
            or elapsed < 0
            or elapsed > minute
            or type(added) is not int
            or added < 0
            or scorer not in (home_id, away_id)
        ):
            raise ValueError
        goals.append((elapsed, added, scorer, detail == "Own Goal"))

    # Own-goal feeds can identify either the scorer's team or the credited team.
    # Reconcile both conventions against the scoreboard and refuse ambiguity.
    candidates = set()
    for invert_own_goals in (False, True):
        total = {home_id: 0, away_id: 0}
        at_threshold = {home_id: 0, away_id: 0}
        first_goal = None
        for elapsed, added, scorer, own in sorted(goals):
            credited = (
                (away_id if scorer == home_id else home_id) if own and invert_own_goals else scorer
            )
            total[credited] += 1
            if elapsed < threshold or (elapsed == threshold and added == 0):
                at_threshold[credited] += 1
                if credited == team_id and first_goal is None:
                    first_goal = elapsed
        if (total[home_id], total[away_id]) == current:
            candidates.add((at_threshold[home_id], at_threshold[away_id], first_goal))
    if len(candidates) != 1:
        return Decision("PENDING", "goal_timeline_inconsistent_or_ambiguous")
    home, away, first_goal = candidates.pop()
    no_goal = first_goal is None
    tied = home == away
    reason = (
        "no_first_goal_and_tie"
        if no_goal and tied
        else ("no_first_goal" if no_goal else "tie" if tied else "scored_and_not_tied")
    )
    return Decision(
        "ALERT_PENDING" if no_goal or tied else "PASSED",
        reason,
        first_goal,
        home,
        away,
        minute,
        current[0],
        current[1],
    )
