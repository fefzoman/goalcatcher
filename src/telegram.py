"""Conservative Telegram delivery: only proven-not-sent failures are retryable."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Delivery:
    outcome: str  # sent, retry, failed, unknown
    message_id: int | None = None
    retry_after: int = 30
    reason: str = ""


def format_alert(watch: dict) -> str:
    heading = "TIE ALERT" if watch["reason"] == "tie" else "FIRST-GOAL ALERT"
    explanations = {
        "no_first_goal": "The team had not scored by its threshold.",
        "tie": "The team scored, but the match was tied at its threshold.",
        "no_first_goal_and_tie": "The team had not scored and the match was tied at its threshold.",
    }
    return (
        f"🚨 {heading}\n\n"
        f"League: {watch['league_name']}\n"
        f"Team: {watch['team_name']}\n"
        f"Match: {watch['home_name']} vs {watch['away_name']}\n\n"
        f"Threshold: {watch['threshold']:g}'\n"
        f"Score at threshold: {watch['threshold_home_goals']}-{watch['threshold_away_goals']}\n"
        f"Latest observed score: {watch['current_home_goals']}-{watch['current_away_goals']}\n"
        f"Match minute: {watch['match_minute']}'\n\n"
        f"{explanations[watch['reason']]}\n"
        f"Reference: {watch['id']}"
    )


class Telegram:
    def __init__(
        self, token: str, chat_id: str, timeout: int = 15, client: httpx.Client | None = None
    ):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    def send(self, text: str) -> Delivery:
        try:
            response = self.client.post(self._url, json={"chat_id": self._chat_id, "text": text})
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return Delivery("retry", reason="connection_not_established")
        except httpx.HTTPError:
            # Read/write failures can occur after Telegram accepted the message.
            return Delivery("unknown", reason="transport_result_unknown")
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            if response.status_code == 200 and payload.get("ok") is True:
                message_id = payload.get("result", {}).get("message_id")
                if type(message_id) is int:
                    return Delivery("sent", message_id=message_id)
            if payload.get("ok") is False:
                error_code = payload.get("error_code")
                if error_code == 429:
                    delay = payload.get("parameters", {}).get("retry_after", 30)
                    if type(delay) is int and delay > 0:
                        return Delivery("retry", retry_after=delay, reason="telegram_rate_limited")
                if error_code in {400, 401, 403, 404}:
                    return Delivery("failed", reason=f"telegram_rejected_{error_code}")
        except (ValueError, TypeError, AttributeError):
            pass
        # Includes 5xx, redirects and malformed success bodies. Never log bodies/URLs.
        return Delivery("unknown", reason="telegram_result_unknown")
