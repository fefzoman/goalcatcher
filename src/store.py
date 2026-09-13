"""Firestore persistence with compare-and-set state changes and delivery claims."""

from __future__ import annotations

from datetime import datetime

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

ACTIVE_STATES = ("PENDING", "ALERT_PENDING", "ALERT_CLAIMED")
TERMINAL_STATES = ("PASSED", "SENT", "SKIPPED", "MISSED_WINDOW", "FAILED", "DELIVERY_UNKNOWN")
TRANSITIONS = {
    "PENDING": {"PENDING", "PASSED", "ALERT_PENDING", "SKIPPED", "MISSED_WINDOW"},
    "ALERT_PENDING": {"ALERT_CLAIMED", "SKIPPED", "MISSED_WINDOW", "FAILED"},
    "ALERT_CLAIMED": {"SENT", "ALERT_PENDING", "FAILED", "DELIVERY_UNKNOWN"},
}


class FirestoreStore:
    def __init__(self, project_id: str, database_id: str = "(default)", client=None):
        self.client = client or firestore.Client(project=project_id, database=database_id)
        self.watches = self.client.collection("watches")

    def close(self) -> None:
        self.client.close()

    def create_watch(self, watch: dict) -> bool:
        """Discovery must never reset an existing watch, including terminal watches."""
        ref = self.watches.document(watch["id"])

        @firestore.transactional
        def create(transaction):
            if ref.get(transaction=transaction).exists:
                return False
            transaction.create(ref, {**watch, "revision": 0})
            return True

        return create(self.client.transaction())

    def active_watches(self) -> list[dict]:
        return [
            snapshot.to_dict()
            for snapshot in self.watches.where(
                filter=FieldFilter("state", "in", ACTIVE_STATES)
            ).stream()
        ]

    def transition(self, watch: dict, fields: dict, now: datetime) -> dict | None:
        """Atomically update only the exact revision we read; return the committed version."""
        if fields.get("state", watch["state"]) not in TRANSITIONS.get(watch["state"], set()):
            raise ValueError("Invalid watch state transition")
        ref = self.watches.document(watch["id"])

        @firestore.transactional
        def update(transaction):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            current = snapshot.to_dict()
            if current["revision"] != watch["revision"] or current["state"] != watch["state"]:
                return None
            changes = {**fields, "updated_at": now, "revision": current["revision"] + 1}
            transaction.update(ref, changes)
            return {**current, **changes}

        return update(self.client.transaction())

    def discovery_done(self, key: str) -> bool:
        return self.client.collection("runtime").document(key).get().exists

    def finish_discovery(self, key: str, now: datetime, sheet_export: str | None = None) -> None:
        fields = {"completed_at": now}
        if sheet_export is not None:
            fields.update(sheet_export_json=sheet_export, sheet_export_pending=True)
        self.client.collection("runtime").document(key).set(fields)

    def pending_sheet_exports(self) -> list[dict]:
        return sorted(
            [
                {**snapshot.to_dict(), "id": snapshot.id}
                for snapshot in self.client.collection("runtime")
                .where(filter=FieldFilter("sheet_export_pending", "==", True))
                .stream()
            ],
            key=lambda job: job["completed_at"],
        )

    def finish_sheet_export(self, key: str, now: datetime) -> None:
        self.client.collection("runtime").document(key).update(
            {"sheet_export_pending": False, "sheet_export_completed_at": now}
        )

    def team_ids(self, config_hash: str) -> dict[str, int]:
        return {
            snapshot.id: data["api_team_id"]
            for snapshot in self.client.collection("team_ids").stream()
            if (data := snapshot.to_dict()).get("config_hash") == config_hash
        }

    def save_team_id(self, key: str, api_id: int, config_hash: str, now: datetime) -> None:
        self.client.collection("team_ids").document(key).set(
            {
                "api_team_id": api_id,
                "config_hash": config_hash,
                "updated_at": now,
            }
        )
