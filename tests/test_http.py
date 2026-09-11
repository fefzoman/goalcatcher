import httpx
import pytest

from src.api_football import FootballAPI, FootballError
from src.telegram import Telegram


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


def test_batches_have_at_most_twenty_unique_ids():
    batches = []

    def handle(request):
        ids = list(map(int, request.url.params["ids"].split("-")))
        batches.append(ids)
        return httpx.Response(
            200, json={"response": [{"id": value} for value in ids], "errors": []}
        )

    api = football_client(handle)
    assert len(api.fixtures(list(range(1, 46)) + [1])) == 45
    assert [len(batch) for batch in batches] == [20, 20, 5]
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
def test_body_errors_not_treated_as_empty_schedule(body):
    api = football_client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(FootballError, match="football_invalid_or_error_response"):
        api.daily_fixtures("2026-09-11", "Europe/Kyiv")


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
