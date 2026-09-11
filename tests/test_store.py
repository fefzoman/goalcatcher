from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from src.monitor import Monitor
from tests.conftest import API, Clock, Messenger, fixture, stored_watch


def test_create_does_not_overwrite_terminal_document(store):
    watch = {"id": "100--city", "state": "SENT"}
    assert store.create_watch(watch)
    assert not store.create_watch({**watch, "state": "PENDING"})
    assert stored_watch(store)["state"] == "SENT"
    assert not store.active_watches()


def test_compare_and_set_requires_current_revision(settings, team, store):
    clock = Clock()
    Monitor(settings, (team,), API([fixture(minute=31)]), store, Messenger(), clock).tick()
    watch = deepcopy(stored_watch(store))
    updated = store.transition(watch, {"reason": "new"}, clock.now)
    assert updated["revision"] == watch["revision"] + 1
    assert store.transition(watch, {"state": "PASSED"}, clock.now) is None


def test_only_one_concurrent_claim_succeeds(store):
    clock = Clock()
    store.create_watch({"id": "100--city", "state": "ALERT_PENDING"})
    watch = deepcopy(stored_watch(store))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: store.transition(watch, {"state": "ALERT_CLAIMED"}, clock.now),
                range(8),
            )
        )
    assert sum(result is not None for result in results) == 1


def test_terminal_state_cannot_reopen(store):
    store.create_watch({"id": "100--city", "state": "SENT"})
    with pytest.raises(ValueError):
        store.transition(stored_watch(store), {"state": "PENDING"}, Clock()())


def test_cached_team_id_scoped_to_config(store):
    store.save_team_id("city", 1, "version1", Clock()())
    assert store.team_ids("version1") == {"city": 1}
    assert store.team_ids("version2") == {}


def test_discovery_checkpoint_is_durable(store):
    assert not store.discovery_done("day")
    store.finish_discovery("day", Clock()())
    assert store.discovery_done("day")
