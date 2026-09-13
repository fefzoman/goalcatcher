import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.config import Settings, Team, fingerprint, load_teams, normalized
from src.monitor import main
from tests.conftest import fixture


def test_all_supplied_thresholds_and_leagues_preserved():
    source = json.loads(Path("thresholds.json").read_text())
    teams = load_teams("config/teams.yaml")
    assert len(teams) == 28
    assert {team.display_name: (team.league, team.threshold) for team in teams} == {
        row["team"]: (row["league"], row["threshold"]) for row in source
    }
    assert len(fingerprint(teams)) == 24


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("key", "../bad"),
        ("threshold", 0),
        ("threshold", 91),
        ("threshold", True),
        ("threshold", float("nan")),
        ("threshold", "32"),
        ("enabled", "false"),
        ("aliases", "Arsenal"),
        ("aliases", [None]),
        ("api_team_id", -1),
        ("api_team_id", True),
        ("display_name", ""),
        ("unknown", 4),
    ],
)
def test_invalid_team_config(tmp_path, field, value):
    team = {
        "key": "city",
        "display_name": "City",
        "country": "England",
        "league": "Premier League",
        "threshold": 32,
    }
    team[field] = value
    path = tmp_path / "teams.yaml"
    path.write_text(yaml.safe_dump({"teams": [team]}))
    with pytest.raises(ValueError):
        load_teams(path)


def test_duplicate_keys_rejected(tmp_path):
    data = yaml.safe_load(Path("config/teams.yaml").read_text())
    data["teams"].append(data["teams"][0])
    path = tmp_path / "teams.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="Duplicate team key"):
        load_teams(path)


def test_exact_accent_normalization_and_id_override():
    assert normalized("Bayern München") == normalized("Bayern Munchen")
    team = Team("city", "MCFC", "England", "Premier League", 32, api_team_id=1)
    row = fixture()
    assert team.matches(row, row["teams"]["home"])
    row["league"]["country"] = "World"
    assert not team.matches(row, row["teams"]["home"])


class SecretClient:
    def __init__(self, value="secret-value"):
        self.value = value
        self.names = []

    def access_secret_version(self, request):
        self.names.append(request["name"])
        return SimpleNamespace(payload=SimpleNamespace(data=self.value.encode()))


def set_required_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")


def test_env_validation_and_secret_repr(monkeypatch):
    set_required_env(monkeypatch)
    client = SecretClient()
    settings = Settings.from_env(secret_client=client)
    assert "secret-value" not in repr(settings)
    assert settings.timezone == "Europe/Kyiv"
    assert client.names == [
        "projects/project/secrets/football-goal-alert-api-football-key/versions/latest",
        "projects/project/secrets/football-goal-alert-telegram-bot-token/versions/latest",
        "projects/project/secrets/football-goal-alert-telegram-chat-id/versions/latest",
    ]


def test_plaintext_secret_environment_variables_are_rejected(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("API_FOOTBALL_KEY", "plaintext-value")
    with pytest.raises(ValueError, match="must be stored in Secret Manager"):
        Settings.from_env(secret_client=SecretClient("managed-value"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("POLL_SECONDS", "0"),
        ("POLL_SECONDS", "oops"),
        ("CLAIM_TIMEOUT_SECONDS", "60"),
        ("TIMEZONE", "not-a-zone"),
        ("API_FOOTBALL_KEY_SECRET_ID", ""),
    ],
)
def test_invalid_settings(monkeypatch, key, value):
    set_required_env(monkeypatch)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.from_env(secret_client=SecretClient())


def test_offline_cli_does_not_require_credentials(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["monitor", "--check-config"])
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    main()
    assert "28 enabled teams" in capsys.readouterr().out
