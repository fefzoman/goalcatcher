"""API-Football v3 adapter. Retries are scheduled by the daemon, not hidden here."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx

from src.config import Team


class FootballError(RuntimeError):
    def __init__(self, reason: str, retry_after: int = 60):
        super().__init__(reason)
        self.retry_after = retry_after


class FootballAPI:
    def __init__(self, api_key: str, timeout: int = 15, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            base_url="https://v3.football.api-sports.io",
            headers={"x-apisports-key": api_key},
            timeout=timeout,
            follow_redirects=False,
        )
        self._leagues: dict[int, dict] = {}

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, params: dict) -> list[dict]:
        try:
            response = self.client.get(path, params=params)
        except httpx.HTTPError:
            raise FootballError("football_transport_error") from None
        if response.status_code != 200:
            try:
                delay = max(30, min(int(response.headers.get("Retry-After", "60")), 86400))
            except ValueError:
                delay = 60
            raise FootballError(f"football_http_{response.status_code}", delay)
        try:
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("errors"):
                raise ValueError
            rows = payload["response"]
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError
            if payload.get("paging", {}).get("total", 1) > 1:
                # The endpoints used here are unpaginated. Never silently drop pages.
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise FootballError("football_invalid_or_error_response") from None
        return rows

    def daily_fixtures(
        self,
        date: str,
        timezone: str,
        *,
        teams: Sequence[Team],
        cached_ids: Mapping[str, int] | None = None,
    ) -> list[dict]:
        """Return selected teams' league fixtures using at most one daily request."""
        enabled = [team for team in teams if team.enabled]
        if not enabled:
            return []
        cached_ids = cached_ids or {}
        rows = self._get("/fixtures", {"date": date, "timezone": timezone})
        return [
            row
            for row in rows
            if any(
                team.matches(row, row["teams"][side], cached_ids.get(team.key))
                for team in enabled
                for side in ("home", "away")
            )
        ]

    def fixtures(self, fixture_ids: list[int]) -> list[dict]:
        """Fetch only requested fixtures using the free-plan-compatible id filter."""
        ids = list(dict.fromkeys(fixture_ids))
        rows = []
        for fixture_id in ids:
            rows.extend(self._get("/fixtures", {"id": fixture_id}))
        return rows

    def events(self, fixture_id: int) -> list[dict]:
        return self._get("/fixtures/events", {"fixture": fixture_id})

    def has_goal_coverage(self, fixture: dict) -> bool:
        league_id = fixture["league"]["id"]
        if league_id not in self._leagues:
            rows = self._get("/leagues", {"id": league_id})
            if len(rows) != 1 or rows[0].get("league", {}).get("id") != league_id:
                raise FootballError("football_league_not_found")
            self._leagues[league_id] = rows[0]
        league = self._leagues[league_id]
        return league.get("league", {}).get("type") == "League" and any(
            season.get("year") == fixture["league"]["season"]
            and season.get("coverage", {}).get("fixtures", {}).get("events") is True
            for season in league.get("seasons", [])
        )

    def refresh_leagues(self) -> None:
        self._leagues.clear()
