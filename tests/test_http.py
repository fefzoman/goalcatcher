from dataclasses import replace

import httpx
import pytest

from src.api_football import FootballAPI, FootballError
from src.telegram import Telegram
from tests.conftest import fixture


def football_client(handler):
    return FootballAPI(
        "secret",
        client=httpx.Client(
            base_url="https://v3.football.api-sports.io",
            transport=httpx.MockTransport(handler),
        ),
    )


def telegram_client(handler):
    return Telegram(
        "secret-token", "chat", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_fixture_refresh_uses_free_plan_id_filter_once_per_unique_fixture():
    calls = []

    def handle(request):
        assert "ids" not in request.url.params
        fixture_id = int(request.url.params["id"])
        calls.append(fixture_id)
        return httpx.Response(
            200, json={"response": [{"fixture": {"id": fixture_id}}], "errors": []}
        )

    api = football_client(handle)
    assert api.fixtures([100, 200, 100]) == [
        {"fixture": {"id": 100}},
        {"fixture": {"id": 200}},
    ]
    assert calls == [100, 200]
    assert api.fixtures([]) == []
    assert calls == [100, 200]
    api.close()


@pytest.mark.parametrize(
    "body",
    [
        {"errors": {"requests": "secret body"}, "response": []},
        {"response": None},
        {"response": [{}], "paging": {"total": 2}},
        [],
    ],
)
def test_body_errors_not_treated_as_empty_schedule(body, team):
    api = football_client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(FootballError, match="football_invalid_or_error_response"):
        api.daily_fixtures("2026-09-11", "Europe/Kyiv", teams=(team,))


def test_daily_fixtures_only_returns_selected_teams_in_their_leagues(team):
    home = fixture(fixture_id=1)
    away = fixture(fixture_id=2)
    away["teams"]["home"], away["teams"]["away"] = (
        away["teams"]["away"],
        away["teams"]["home"],
    )
    unrelated = fixture(fixture_id=3)
    unrelated["teams"]["home"] = {"id": 3, "name": "Chelsea"}
    cup = fixture(fixture_id=4)
    cup["league"]["name"] = "FA Cup"
    foreign = fixture(fixture_id=5)
    foreign["league"]["country"] = "World"
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"response": [home, away, unrelated, cup, foreign]})

    api = football_client(handle)
    disabled = replace(team, key="chelsea", display_name="Chelsea", enabled=False)
    result = api.daily_fixtures("2026-09-11", "Europe/Kyiv", teams=(team, disabled))
    assert [row["fixture"]["id"] for row in result] == [1, 2]
    assert len(calls) == 1
    assert calls[0].url.path == "/fixtures"
    assert dict(calls[0].url.params) == {"date": "2026-09-11", "timezone": "Europe/Kyiv"}
    api.close()


@pytest.mark.parametrize("selection", ["aliases", "pinned_ids", "cached_ids", "both_sides"])
def test_daily_fixtures_preserves_team_matching_and_returns_each_fixture_once(team, selection):
    row = fixture()
    cached_ids = None
    if selection == "aliases":
        teams = (
            replace(
                team,
                display_name="City",
                aliases=("Manchester City",),
                league="English League",
                league_aliases=("Premier League",),
            ),
        )
    elif selection == "pinned_ids":
        teams = (
            replace(
                team, display_name="City", league="English League", api_team_id=1, api_league_id=39
            ),
        )
    elif selection == "cached_ids":
        teams = (replace(team, display_name="City"),)
        cached_ids = {team.key: 1}
    else:
        teams = (team, replace(team, key="arsenal", display_name="Arsenal"))
    api = football_client(lambda request: httpx.Response(200, json={"response": [row]}))
    assert api.daily_fixtures("2026-09-11", "Europe/Kyiv", teams=teams, cached_ids=cached_ids) == [
        row
    ]
    api.close()


@pytest.mark.parametrize("disabled", [True, False])
def test_daily_fixtures_without_enabled_teams_uses_no_requests(team, disabled):
    def handle(request):
        pytest.fail("An empty team selection must not consume an API request")

    api = football_client(handle)
    teams = (replace(team, enabled=False),) if disabled else ()
    assert api.daily_fixtures("2026-09-11", "Europe/Kyiv", teams=teams) == []
    api.close()


def test_rate_limit_exposes_delay_without_response_body():
    api = football_client(
        lambda request: httpx.Response(429, text="secret", headers={"Retry-After": "123"})
    )
    with pytest.raises(FootballError) as error:
        api.events(123)
    assert error.value.retry_after == 123
    assert "secret" not in str(error.value)


def test_football_transport_failure_is_safe_to_retry():
    def handle(request):
        raise httpx.ReadTimeout("secret", request=request)

    with pytest.raises(FootballError, match="football_transport_error"):
        football_client(handle).events(1)


@pytest.mark.parametrize(
    ("league_type", "coverage", "expected"),
    [
        ("League", True, True),
        ("Cup", True, False),
        ("League", False, False),
    ],
)
def test_domestic_league_and_season_coverage(league_type, coverage, expected):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "response": [
                    {
                        "league": {"id": 39, "type": league_type},
                        "seasons": [{"year": 2026, "coverage": {"fixtures": {"events": coverage}}}],
                    }
                ]
            },
        )

    api = football_client(handle)
    row = {"league": {"id": 39, "season": 2026}}
    assert api.has_goal_coverage(row) is expected
    assert api.has_goal_coverage(row) is expected
    assert len(calls) == 1
    api.refresh_leagues()
    api.has_goal_coverage(row)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("status", "body", "outcome"),
    [
        (200, {"ok": True, "result": {"message_id": 10}}, "sent"),
        (429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}, "retry"),
        (400, {"ok": False, "error_code": 400}, "failed"),
        (401, {"ok": False, "error_code": 401}, "failed"),
        (403, {"ok": False, "error_code": 403}, "failed"),
        (500, {"ok": False, "error_code": 500}, "unknown"),
        (200, {"ok": True}, "unknown"),
        (200, {}, "unknown"),
        (200, [], "unknown"),
        (302, {}, "unknown"),
    ],
)
def test_telegram_outcomes(status, body, outcome):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    sender = telegram_client(handle)
    result = sender.send("hello")
    assert result.outcome == outcome
    assert len(calls) == 1  # never retry automatically inside the HTTP client
    assert "secret-token" not in repr(result)
    sender.close()


@pytest.mark.parametrize(
    ("exception", "outcome"),
    [
        (httpx.ConnectTimeout, "retry"),
        (httpx.ConnectError, "retry"),
        (httpx.PoolTimeout, "retry"),
        (httpx.ReadTimeout, "unknown"),
        (httpx.WriteTimeout, "unknown"),
        (httpx.ReadError, "unknown"),
    ],
)
def test_telegram_transport_certainty(exception, outcome):
    def handle(request):
        raise exception("sensitive URL", request=request)

    assert telegram_client(handle).send("hi").outcome == outcome


def test_malformed_telegram_response_is_unknown():
    assert (
        telegram_client(lambda request: httpx.Response(200, text="not-json")).send("hi").outcome
        == "unknown"
    )
