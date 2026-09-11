from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock

import pytest

from src.config import Settings, Team
from src.store import FirestoreStore
from src.telegram import Delivery


def event(minute, team=1, detail="Normal Goal", extra=None, kind="Goal"):
    return {
        "type": kind,
        "detail": detail,
        "time": {"elapsed": minute, "extra": extra},
        "team": {"id": team},
    }


def fixture(minute=33, events=None, score=None, status="1H", fixture_id=100, kickoff=None):
    events = events if events is not None else []
    if score is None:
        score = tuple(
            sum(
                row["type"] == "Goal"
                and row["detail"] != "Missed Penalty"
                and row["team"]["id"] == team
                for row in events
            )
            for team in (1, 2)
        )
    return {
        "fixture": {
            "id": fixture_id,
            "date": kickoff or "2026-09-11T12:00:00+00:00",
            "status": {"short": status, "elapsed": minute, "extra": None},
        },
        "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2026},
        "teams": {
            "home": {"id": 1, "name": "Manchester City"},
            "away": {"id": 2, "name": "Arsenal"},
        },
        "goals": {"home": score[0], "away": score[1]},
        "events": events,
    }


class Snapshot:
    def __init__(self, key, value):
        self.id, self.value = key, deepcopy(value)
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self.value)


class Reference:
    def __init__(self, collection, key):
        self.collection, self.id = collection, key

    def get(self, transaction=None):
        return Snapshot(self.id, self.collection.rows.get(self.id))

    def set(self, value):
        self.collection.rows[self.id] = deepcopy(value)


class Collection:
    def __init__(self):
        self.rows = {}

    def document(self, key):
        return Reference(self, key)

    def stream(self):
        return [Snapshot(key, value) for key, value in self.rows.items()]

    def where(self, *, filter):
        assert filter.field_path == "state" and filter.op_string == "in"
        collection = Collection()
        collection.rows = {
            key: value for key, value in self.rows.items() if value["state"] in filter.value
        }
        return collection


class Transaction:
    def __init__(self, lock):
        self.lock = lock

    def create(self, ref, value):
        assert not ref.get().exists
        ref.set(value)

    def update(self, ref, changes):
        assert ref.get().exists
        ref.set({**ref.get().to_dict(), **changes})


class MemoryClient:
    """Test double, not a production store or Firestore contention emulator."""

    def __init__(self):
        self.collections = {}
        self.lock = RLock()

    def collection(self, name):
        return self.collections.setdefault(name, Collection())

    def transaction(self):
        return Transaction(self.lock)

    def close(self):
        pass


@pytest.fixture
def store(monkeypatch):
    def transactional(fn):
        def invoke(transaction):
            with transaction.lock:
                return fn(transaction)

        return invoke

    monkeypatch.setattr("src.store.firestore.transactional", transactional)
    return FirestoreStore("unit-test", client=MemoryClient())


@pytest.fixture
def settings():
    return Settings(
        "unit-test", "api-secret", "telegram-secret", "test-chat", confirmation_seconds=0
    )


@pytest.fixture
def team():
    return Team("manchester_city", "Manchester City", "England", "Premier League", 32)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 11, 12, 33, tzinfo=UTC)

    def __call__(self):
        return self.now


class API:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [fixture()]
        self.daily_calls = []
        self.live_calls = []
        self.coverage = True
        self.error = None

    def refresh_leagues(self):
        pass

    def daily_fixtures(self, day, timezone):
        self.daily_calls.append((day, timezone))
        if self.error:
            raise self.error
        return deepcopy(self.rows)

    def has_goal_coverage(self, row):
        return self.coverage

    def fixtures(self, ids):
        self.live_calls.append(ids)
        if self.error:
            raise self.error
        return deepcopy([row for row in self.rows if row["fixture"]["id"] in ids])

    def events(self, fixture_id):
        return []


class Messenger:
    def __init__(self, *results):
        self.messages = []
        self.results = list(results) or [Delivery("sent", message_id=987)]

    def send(self, text):
        self.messages.append(text)
        return self.results.pop(0)


def stored_watch(store):
    return next(iter(store.watches.rows.values()))
