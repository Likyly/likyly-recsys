"""Short alerts to the team's private channel, e.g. "a new account just signed up".

Configured through the environment (application/api/.env, written by the deploy workflow):
    TEAM_ALERT_WEBHOOK_URL   a Discord webhook (Server settings > Integrations > Webhooks) or a Slack
                             incoming webhook. Unset: alerts are skipped, nothing else changes.

The payload carries both `content` (Discord) and `text` (Slack), so either kind of webhook works.
Best effort: called as a background task, it never fails the request that triggered it.
"""
import json
import os
import urllib.request

from observability import log_event

TIMEOUT_SECONDS = 10


def send_team_alert(message: str) -> bool:
    url = os.environ.get("TEAM_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return False
    body = json.dumps({"content": message[:1900], "text": message, "allowed_mentions": {"parse": []}}).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "likyly-api"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            ok = 200 <= response.status < 300
    except Exception as exc:  # noqa: BLE001 - an alert must never break anything
        log_event("team_alert_failed", error=type(exc).__name__)
        return False
    if not ok:
        log_event("team_alert_failed", status=response.status)
    return ok


def new_account_message(email: str | None, client_id: int) -> str:
    who = email or "(e-mail inconnu)"
    return f"🆕 Nouveau compte LIKYLY : **{who}** (workspace #{client_id})\nhttps://www.likyly.com/admin/clients"
