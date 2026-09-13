"""Validated, immutable application and team configuration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import secretmanager

SECRET_IDS = {
    "API_FOOTBALL_KEY_SECRET_ID": "football-goal-alert-api-football-key",
    "TELEGRAM_BOT_TOKEN_SECRET_ID": "football-goal-alert-telegram-bot-token",
    "TELEGRAM_CHAT_ID_SECRET_ID": "football-goal-alert-telegram-chat-id",
}
PLAINTEXT_SECRET_NAMES = ("API_FOOTBALL_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(c for c in value if c.isalnum() and not unicodedata.combining(c))


@dataclass(frozen=True)
class Team:
    key: str
    display_name: str
    country: str
    league: str
    threshold: float
    enabled: bool = True
    aliases: tuple[str, ...] = ()
    league_aliases: tuple[str, ...] = ()
    api_team_id: int | None = None
    api_league_id: int | None = None

    def matches(self, fixture: dict, side: dict, cached_id: int | None = None) -> bool:
        league = fixture["league"]
        if normalized(league["country"]) != normalized(self.country):
            return False
        if self.api_league_id is not None:
            if league["id"] != self.api_league_id:
                return False
        elif normalized(league["name"]) not in {
            normalized(name) for name in (self.league, *self.league_aliases)
        }:
            return False
        expected_id = self.api_team_id or cached_id
        return (
            side["id"] == expected_id
            if expected_id is not None
            else normalized(side["name"])
            in {normalized(name) for name in (self.display_name, *self.aliases)}
        )


def load_teams(path: str | Path) -> tuple[Team, ...]:
    with Path(path).open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict) or not isinstance(document.get("teams"), list):
        raise ValueError("Team configuration must contain a teams list")
    teams = []
    keys = set()
    for item in document["teams"]:
        if not isinstance(item, dict):
            raise ValueError("Each team must be a mapping")
        values = dict(item)
        for name in ("key", "display_name", "country", "league"):
            if not isinstance(values.get(name), str) or not values[name].strip():
                raise ValueError(f"Team requires a nonempty {name}")
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", values["key"]):
            raise ValueError("Team keys must be lowercase snake_case")
        if values["key"] in keys:
            raise ValueError(f"Duplicate team key: {values['key']}")
        keys.add(values["key"])
        threshold = values.get("threshold")
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or not 0 < threshold <= 90
        ):
            raise ValueError(f"Invalid threshold for {values['key']}: expected 0 < minute <= 90")
        if not isinstance(values.get("enabled", True), bool):
            raise ValueError("enabled must be a boolean")
        for name in ("aliases", "league_aliases"):
            aliases = values.get(name, [])
            if not isinstance(aliases, list) or not all(
                isinstance(alias, str) and alias.strip() for alias in aliases
            ):
                raise ValueError(f"{name} must be a list of nonempty strings")
            values[name] = tuple(aliases)
        for name in ("api_team_id", "api_league_id"):
            value = values.get(name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"{name} must be a positive integer")
        try:
            teams.append(Team(**values))
        except TypeError as exc:
            raise ValueError("Unknown team configuration field") from exc
    if not any(team.enabled for team in teams):
        raise ValueError("Enable at least one team")
    return tuple(teams)


def fingerprint(teams: tuple[Team, ...]) -> str:
    data = json.dumps([asdict(team) for team in teams], sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Settings:
    project_id: str
    api_key: str = field(repr=False)
    telegram_token: str = field(repr=False)
    telegram_chat_id: str = field(repr=False)
    database_id: str = "(default)"
    timezone: str = "Europe/Kyiv"
    poll_seconds: int = 30
    precheck_seconds: int = 30
    confirmation_seconds: int = 60
    max_lateness_minutes: int = 5
    max_fixture_age_hours: int = 6
    claim_timeout_seconds: int = 120
    request_timeout_seconds: int = 15
    max_delivery_attempts: int = 5
    sheets_spreadsheet_id: str = "1-gZnwabNdLarv8ofDXRDh0XOzKa6g4Ozyx202ZPKEx8"

    @classmethod
    def from_env(cls, *, secret_client=None) -> Settings:
        plaintext = next((name for name in PLAINTEXT_SECRET_NAMES if name in os.environ), None)
        if plaintext:
            raise ValueError(f"{plaintext} must be stored in Secret Manager, not the environment")
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if not project_id:
            raise ValueError("Missing environment variable: GOOGLE_CLOUD_PROJECT")
        secret_ids = {}
        for name, default in SECRET_IDS.items():
            secret_id = os.environ.get(name, default).strip()
            if not secret_id:
                raise ValueError(f"Missing environment variable: {name}")
            secret_ids[name] = secret_id
        try:
            secret_client = secret_client or secretmanager.SecretManagerServiceClient()
            secrets = {
                name: secret_client.access_secret_version(
                    request={"name": f"projects/{project_id}/secrets/{secret_id}/versions/latest"}
                ).payload.data.decode()
                for name, secret_id in secret_ids.items()
            }
        except (GoogleAPICallError, GoogleAuthError, UnicodeDecodeError, AttributeError):
            raise ValueError("Could not load application secrets from Secret Manager") from None
        empty = next((name for name, value in secrets.items() if not value.strip()), None)
        if empty:
            raise ValueError(f"Secret has no value: {empty}")
        numbers = {}
        for name, default, minimum, maximum in (
            ("poll_seconds", 30, 5, 300),
            ("precheck_seconds", 30, 0, 300),
            ("confirmation_seconds", 60, 0, 180),
            ("max_lateness_minutes", 5, 2, 30),
            ("max_fixture_age_hours", 6, 3, 24),
            ("claim_timeout_seconds", 120, 60, 3600),
            ("request_timeout_seconds", 15, 1, 30),
            ("max_delivery_attempts", 5, 1, 20),
        ):
            value = int(os.environ.get(name.upper(), default))
            if not minimum <= value <= maximum:
                raise ValueError(f"{name.upper()} must be between {minimum} and {maximum}")
            numbers[name] = value
        if numbers["claim_timeout_seconds"] <= 4 * numbers["request_timeout_seconds"]:
            raise ValueError("CLAIM_TIMEOUT_SECONDS must exceed four request timeouts")
        if numbers["confirmation_seconds"] >= numbers["max_lateness_minutes"] * 60:
            raise ValueError("Confirmation must be shorter than the alert lateness window")
        timezone = os.environ.get("TIMEZONE", "Europe/Kyiv")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("TIMEZONE must be an installed IANA timezone") from exc
        return cls(
            project_id=project_id,
            api_key=secrets["API_FOOTBALL_KEY_SECRET_ID"].strip(),
            telegram_token=secrets["TELEGRAM_BOT_TOKEN_SECRET_ID"].strip(),
            telegram_chat_id=secrets["TELEGRAM_CHAT_ID_SECRET_ID"].strip(),
            database_id=os.environ.get("FIRESTORE_DATABASE_ID", "(default)"),
            timezone=timezone,
            sheets_spreadsheet_id=os.environ.get(
                "GOOGLE_SHEETS_SPREADSHEET_ID", cls.sheets_spreadsheet_id
            ).strip(),
            **numbers,
        )
