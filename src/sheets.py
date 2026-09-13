"""Append selected-fixture snapshots to the matches tab in Google Sheets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo

import google.auth
import httpx
from dotenv import load_dotenv
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request

from src.config import Settings, Team, load_teams
from src.evaluator import evaluate

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
LEGACY_HEADERS = [
    "Fixture ID",
    "Kickoff (local)",
    "Timezone",
    "Country",
    "League",
    "Round",
    "Home team",
    "Away team",
    "Selected teams",
    "Thresholds (minutes)",
    "Status",
    "Home goals",
    "Away goals",
]
HEADERS = [*LEGACY_HEADERS, "Alert name"]
NO_GOAL_REASONS = {"no_first_goal", "no_first_goal_and_tie"}


class SheetsError(RuntimeError):
    pass


def match_rows(
    fixtures: list[dict],
    teams: tuple[Team, ...],
    timezone: str,
    cached_ids: dict[str, int] | None = None,
) -> list[list]:
    """One row per selected fixture, with null scores kept blank and literal text."""
    rows = {}
    cached_ids = cached_ids or {}
    for fixture in fixtures:
        selected = []
        for team in teams:
            if not team.enabled:
                continue
            matched_ids = [
                fixture["teams"][side]["id"]
                for side in ("home", "away")
                if team.matches(fixture, fixture["teams"][side], cached_ids.get(team.key))
            ]
            if matched_ids:
                selected.append((team, matched_ids[0]))
        if not selected:
            continue
        reasons = {
            evaluate(fixture, api_team_id, team.threshold).reason for team, api_team_id in selected
        }
        alert_name = "NO GOAL" if reasons & NO_GOAL_REASONS else "TIE" if "tie" in reasons else ""
        details, league = fixture["fixture"], fixture["league"]
        kickoff = datetime.fromisoformat(details["date"].replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            raise ValueError("Fixture kickoff requires an offset")
        local = kickoff.astimezone(ZoneInfo(timezone))
        rows[details["id"]] = [
            details["id"],
            local.strftime("%Y-%m-%d %H:%M"),
            timezone,
            league["country"],
            league["name"],
            league.get("round", ""),
            fixture["teams"]["home"]["name"],
            fixture["teams"]["away"]["name"],
            "; ".join(team.display_name for team, _ in selected),
            "; ".join(f"{team.display_name}: {team.threshold:g}" for team, _ in selected),
            details["status"].get("long") or details["status"]["short"],
            fixture["goals"]["home"],
            fixture["goals"]["away"],
            alert_name,
        ]
    return sorted(rows.values(), key=lambda row: (row[1], row[0]))


class GoogleSheets:
    def __init__(self, spreadsheet_id: str, timeout: int = 15, *, credentials=None, client=None):
        self.spreadsheet_id = spreadsheet_id
        self.timeout = timeout
        self.credentials = credentials
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self.auth_request = Request()

    def close(self) -> None:
        self.client.close()
        self.auth_request.session.close()

    def _request(self, method: str, suffix: str = "", **kwargs) -> dict:
        try:
            if self.credentials is None:
                self.credentials, _ = google.auth.default(scopes=[SHEETS_SCOPE])
            if not self.credentials.valid:
                self.credentials.refresh(partial(self.auth_request, timeout=self.timeout))
            headers = {}
            self.credentials.apply(headers)
            response = self.client.request(
                method,
                f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}{suffix}",
                headers=headers,
                **kwargs,
            )
        except GoogleAuthError:
            raise SheetsError("sheets_auth_error") from None
        except httpx.HTTPError:
            raise SheetsError("sheets_transport_error") from None
        if response.status_code != 200:
            raise SheetsError(f"sheets_http_{response.status_code}")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except ValueError:
            raise SheetsError("sheets_invalid_response") from None

    def export(self, export_id: str, rows: list[list]) -> None:
        """Append a new fetch; an atomic metadata marker makes retries idempotent."""
        if not rows:
            return
        # Pending payloads written by the previous release have 13 columns.
        rows = [[*row, ""] if len(row) == len(LEGACY_HEADERS) else row for row in rows]
        if any(len(row) != len(HEADERS) for row in rows):
            raise SheetsError("sheets_row_schema_invalid")
        title = "matches"
        metadata = self._request(
            "GET",
            params={
                "fields": "sheets.properties,developerMetadata",
            },
        )
        markers = metadata.get("developerMetadata", [])
        if any(
            marker.get("metadataKey") == "goalcatcher_export"
            and marker.get("metadataValue") == export_id
            for marker in markers
        ):
            return
        # Explicit unique metadata IDs make concurrent retries fail atomically
        # before appending. Probe around IDs already used by other exports/apps.
        marker_id = int.from_bytes(hashlib.sha256(export_id.encode()).digest()[:4]) & 0x7FFFFFFF
        used_ids = {marker["metadataId"] for marker in markers}
        while marker_id in used_ids:
            marker_id = (marker_id + 1) & 0x7FFFFFFF
        sheets = [sheet["properties"] for sheet in metadata.get("sheets", [])]
        existing = next((sheet for sheet in sheets if sheet["title"] == title), None)
        requests = []
        include_header = existing is None
        if existing is None:
            sheet_id = max((sheet["sheetId"] for sheet in sheets), default=0) + 1
            requests.append(
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": sheet_id,
                            "title": title,
                            "gridProperties": {
                                "rowCount": max(100, len(rows) + 1),
                                "columnCount": len(HEADERS),
                                "frozenRowCount": 1,
                            },
                        }
                    }
                }
            )
        else:
            sheet_id = existing["sheetId"]
            grid = existing["gridProperties"]
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "columnCount": max(grid["columnCount"], len(HEADERS)),
                            },
                        },
                        "fields": "gridProperties.columnCount",
                    }
                }
            )
            # Existing rows remain untouched; incompatible columns need review.
            current = self._request("GET", f"/values/'{title}'!A1:N1")
            include_header = not current.get("values")
            if include_header:
                occupied = self._request("GET", f"/values/'{title}'!A:N")
                if occupied.get("values"):
                    raise SheetsError("sheets_header_conflict")
            elif current["values"] == [LEGACY_HEADERS]:
                existing_values = self._request("GET", f"/values/'{title}'!A:N").get("values", [])
                requests.append(
                    {
                        "updateCells": {
                            "start": {
                                "sheetId": sheet_id,
                                "rowIndex": 0,
                                "columnIndex": len(LEGACY_HEADERS),
                            },
                            "rows": [
                                {
                                    "values": [
                                        {
                                            "userEnteredValue": {
                                                "stringValue": ("Alert name" if index == 0 else "")
                                            }
                                        }
                                    ]
                                }
                                for index, row in enumerate(existing_values)
                            ],
                            "fields": "userEnteredValue",
                        }
                    }
                )
            elif current["values"] != [HEADERS]:
                raise SheetsError("sheets_header_conflict")

        def cell(value):
            if value is None:
                return {}
            key = "numberValue" if isinstance(value, (int, float)) else "stringValue"
            return {"userEnteredValue": {key: value}}

        if include_header:
            requests.append(
                {
                    "updateCells": {
                        "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
                        "rows": [{"values": [cell(value) for value in HEADERS]}],
                        "fields": "userEnteredValue",
                    }
                }
            )
        requests.append(
            {
                "appendCells": {
                    "sheetId": sheet_id,
                    "rows": [{"values": [cell(value) for value in row]} for row in rows],
                    "fields": "userEnteredValue",
                }
            }
        )
        requests.append(
            {
                "createDeveloperMetadata": {
                    "developerMetadata": {
                        "metadataId": marker_id,
                        "metadataKey": "goalcatcher_export",
                        "metadataValue": export_id,
                        "location": {"spreadsheet": True},
                        "visibility": "DOCUMENT",
                    }
                }
            }
        )
        if existing is None:
            requests.extend(
                [
                    {
                        "repeatCell": {
                            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                            "fields": "userEnteredFormat.textFormat.bold",
                        }
                    },
                    {
                        "autoResizeDimensions": {
                            "dimensions": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": len(HEADERS),
                            }
                        }
                    },
                ]
            )
        self._request("POST", ":batchUpdate", json={"requests": requests})


def main() -> None:
    parser = argparse.ArgumentParser(description="Append cached selected matches to Google Sheets")
    parser.add_argument("--fixtures-json", required=True, type=Path)
    parser.add_argument("--config", default="config/teams.yaml")
    args = parser.parse_args()
    load_dotenv(override=False)
    spreadsheet_id = os.environ.get(
        "GOOGLE_SHEETS_SPREADSHEET_ID", Settings.sheets_spreadsheet_id
    ).strip()
    if not spreadsheet_id:
        parser.error("GOOGLE_SHEETS_SPREADSHEET_ID is empty")
    rows = match_rows(
        json.loads(args.fixtures_json.read_text(encoding="utf-8")),
        load_teams(args.config),
        os.environ.get("TIMEZONE", "Europe/Kyiv"),
    )
    sheets = GoogleSheets(spreadsheet_id)
    try:
        sheets.export(str(uuid.uuid4()), rows)
    except SheetsError as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    finally:
        sheets.close()
    print(f"Appended {len(rows)} matches to the matches tab; football API requests: 0")


if __name__ == "__main__":
    main()
