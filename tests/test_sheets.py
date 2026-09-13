import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from google.oauth2.credentials import Credentials

from src.monitor import Monitor
from src.sheets import HEADERS, LEGACY_HEADERS, GoogleSheets, SheetsError, match_rows
from tests.conftest import API, Clock, Messenger, event, fixture


class SheetServer:
    def __init__(self, existing=True):
        self.sheets = (
            [
                {
                    "properties": {
                        "sheetId": 7,
                        "title": "matches",
                        "gridProperties": {
                            "rowCount": 100,
                            "columnCount": len(HEADERS),
                        },
                    }
                }
            ]
            if existing
            else []
        )
        self.values = [HEADERS.copy()] if existing else []
        self.markers = []
        self.batches = []
        self.lose_response = False

    def handle(self, request):
        assert request.headers["authorization"] == "Bearer unit-token"
        if request.method == "GET":
            if "/values/" in request.url.path:
                values = self.values[:1] if request.url.path.endswith("A1:N1") else self.values
                return httpx.Response(200, json={"values": values})
            return httpx.Response(
                200,
                json={
                    "sheets": self.sheets,
                    "developerMetadata": self.markers,
                },
            )
        assert request.url.path.endswith(":batchUpdate")
        batch = json.loads(request.content)["requests"]
        self.batches.append(batch)
        for update in batch:
            if "addSheet" in update:
                self.sheets.append(update["addSheet"])
            if "createDeveloperMetadata" in update:
                self.markers.append(update["createDeveloperMetadata"]["developerMetadata"])
            for operation in ("updateCells", "appendCells"):
                if operation not in update:
                    continue
                values = [
                    [
                        next(iter(cell.get("userEnteredValue", {}).values()), None)
                        for cell in row["values"]
                    ]
                    for row in update[operation]["rows"]
                ]
                if operation == "updateCells":
                    start = update[operation]["start"]
                    row_index = start.get("rowIndex", 0)
                    column_index = start.get("columnIndex", 0)
                    for offset, row in enumerate(values):
                        target = row_index + offset
                        while len(self.values) <= target:
                            self.values.append([])
                        while len(self.values[target]) < column_index:
                            self.values[target].append(None)
                        for column_offset, value in enumerate(row):
                            column = column_index + column_offset
                            while len(self.values[target]) <= column:
                                self.values[target].append(None)
                            self.values[target][column] = value
                else:
                    self.values.extend(values)
        if self.lose_response:
            self.lose_response = False
            raise httpx.ReadTimeout("sensitive response", request=request)
        return httpx.Response(200, json={})


def client(handler):
    return GoogleSheets(
        "spreadsheet",
        credentials=Credentials(token="unit-token"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_rows_filter_deduplicate_and_preserve_timezone_null_scores_and_both_teams(team):
    selected = fixture(score=(None, 0))
    unrelated = fixture(fixture_id=2)
    unrelated["teams"]["home"]["name"] = "Chelsea"
    cup = fixture(fixture_id=3)
    cup["league"]["name"] = "FA Cup"
    other = replace(team, key="arsenal", display_name="Arsenal")
    result = match_rows([selected, deepcopy(selected), unrelated, cup], (team,), "Europe/Kyiv")
    assert len(result) == 1
    assert result[0][1:3] == ["2026-09-11 15:00", "Europe/Kyiv"]
    assert result[0][-3:-1] == [None, 0]
    assert result[0][-1] == ""
    both = match_rows([selected], (team, other), "Europe/Kyiv")
    assert len(both) == 1
    assert both[0][8] == "Manchester City; Arsenal"
    assert both[0][-1] == ""


@pytest.mark.parametrize(
    ("events", "score", "expected"),
    [
        ([], (0, 0), "NO GOAL"),
        ([event(10, 2)], (0, 1), "NO GOAL"),
        ([event(10), event(31, 2)], (1, 1), "TIE"),
        ([event(10)], (1, 0), ""),
    ],
)
def test_alert_name_uses_threshold_rules(team, events, score, expected):
    row = match_rows([fixture(events=events, score=score)], (team,), "Europe/Kyiv")[0]
    assert row[-1] == expected


def test_alert_name_is_empty_until_rule_can_be_evaluated(team):
    before_threshold = fixture(minute=31, events=[], score=(0, 0))
    incomplete = fixture(events=[], score=(0, 0))
    del incomplete["events"]
    assert match_rows([before_threshold], (team,), "Europe/Kyiv")[0][-1] == ""
    assert match_rows([incomplete], (team,), "Europe/Kyiv")[0][-1] == ""


def test_no_goal_takes_precedence_for_two_selected_teams(team):
    arsenal = replace(team, key="arsenal", display_name="Arsenal")
    row = match_rows(
        [fixture(events=[event(10)], score=(1, 0))],
        (team, arsenal),
        "Europe/Kyiv",
    )[0]
    assert row[-1] == "NO GOAL"


def test_existing_sheet_schema_is_upgraded_with_empty_historical_alert_names(team):
    server = SheetServer()
    old_row = match_rows([fixture()], (team,), "Europe/Kyiv")[0][:-1]
    expected_old_row = [*old_row, ""]
    server.values = [LEGACY_HEADERS.copy(), old_row]
    sheets = client(server.handle)
    new_rows = match_rows([fixture(fixture_id=200)], (team,), "Europe/Kyiv")
    sheets.export("schema-upgrade", new_rows)
    assert server.values == [HEADERS, expected_old_row, *new_rows]
    assert "updateSheetProperties" in server.batches[0][0]
    assert "updateCells" in server.batches[0][1]
    sheets.close()


def test_pending_legacy_rows_gain_alert_name_before_append(team):
    server = SheetServer()
    sheets = client(server.handle)
    old_row = match_rows([fixture()], (team,), "Europe/Kyiv")[0][:-1]
    sheets.export("legacy-job", [old_row])
    assert server.values == [HEADERS, [*old_row, ""]]
    sheets.close()


@pytest.mark.parametrize("existing", [True, False])
def test_each_fetch_appends_to_matches_and_retains_existing_rows(team, existing):
    server = SheetServer(existing)
    sheets = client(server.handle)
    rows = match_rows([fixture()], (team,), "Europe/Kyiv")
    sheets.export("run-1", rows)
    first = deepcopy(server.values)
    sheets.export("run-2", rows)
    assert server.values == [*first, *rows]
    assert server.values == [HEADERS, *rows, *rows]
    assert server.sheets[0]["properties"]["title"] == "matches"
    assert len(server.markers) == 2
    sheets.close()


def test_retry_after_committed_append_timeout_does_not_duplicate_rows(team):
    server = SheetServer()
    sheets = client(server.handle)
    rows = match_rows([fixture()], (team,), "Europe/Kyiv")
    server.lose_response = True
    with pytest.raises(SheetsError, match="sheets_transport_error"):
        sheets.export("same-run", rows)
    sheets.export("same-run", rows)
    assert server.values == [HEADERS, *rows]
    assert len(server.batches) == 1
    sheets.close()


def test_incompatible_existing_headers_are_preserved(team):
    server = SheetServer()
    server.values = [["Existing notes"]]
    sheets = client(server.handle)
    with pytest.raises(SheetsError, match="sheets_header_conflict"):
        sheets.export("run", match_rows([fixture()], (team,), "Europe/Kyiv"))
    assert server.values == [["Existing notes"]]
    assert not server.batches
    sheets.close()


def test_text_is_literal_and_empty_export_makes_no_requests(team):
    server = SheetServer()
    sheets = client(server.handle)
    row = fixture()
    row["teams"]["away"]["name"] = '=IMPORTXML("https://example.invalid", "x")'
    sheets.export("run", match_rows([row], (team,), "Europe/Kyiv"))
    appended = next(r["appendCells"] for r in server.batches[0] if "appendCells" in r)
    assert appended["rows"][0]["values"][7]["userEnteredValue"] == {
        "stringValue": row["teams"]["away"]["name"],
    }
    sheets.close()
    sheets = client(lambda request: pytest.fail("Empty exports should make no requests"))
    sheets.export("empty", [])
    sheets.close()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_errors_are_safe_and_do_not_retry_immediately(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive provider details")

    sheets = client(handle)
    with pytest.raises(SheetsError, match=f"^sheets_http_{status}$"):
        sheets.export("run", [[1] * len(HEADERS)])
    assert len(calls) == 1
    sheets.close()


def test_export_failure_recovers_after_restart_without_refetching(settings, team, store):
    clock, api, server = Clock(), API([fixture(minute=31)]), SheetServer()
    sheets = client(lambda request: httpx.Response(403, json={}))
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock, sheets=sheets)
    monitor.tick()
    assert len(api.daily_calls) == 2
    assert len(store.pending_sheet_exports()) == 2
    assert all(store.discovery_done(job["id"]) for job in store.pending_sheet_exports())
    assert monitor.sheets_not_before == clock.now + timedelta(seconds=60)
    monitor.tick()
    assert len(api.daily_calls) == 2
    sheets.close()
    sheets = client(server.handle)
    restarted = Monitor(settings, (team,), api, store, Messenger(), clock, sheets=sheets)
    restarted.tick()
    restarted.tick()
    assert not store.pending_sheet_exports()
    assert len(api.daily_calls) == 2
    assert len(server.values) == 3  # Header plus both successful daily fetches.
    sheets.close()


def test_export_commit_survives_firestore_ack_failure(settings, team, store, monkeypatch):
    clock, api, server = Clock(), API([fixture(minute=31)]), SheetServer()
    sheets = client(server.handle)
    monitor = Monitor(settings, (team,), api, store, Messenger(), clock, sheets=sheets)
    original = store.finish_sheet_export

    def fail(*args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(store, "finish_sheet_export", fail)
    with pytest.raises(RuntimeError, match="write failed"):
        monitor.tick()
    assert len(server.values) == 2
    monkeypatch.setattr(store, "finish_sheet_export", original)
    monitor.tick()
    assert len(server.values) == 2
    assert len(api.daily_calls) == 2
    sheets.close()
