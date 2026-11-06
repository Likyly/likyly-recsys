"""New sign-up alert: the first workspace of an account posts one message to TEAM_ALERT_WEBHOOK_URL;
later calls do not, and nothing is sent (or broken) when the webhook is not configured."""
import uuid

import pytest

import alerts
import app as app_module


@pytest.fixture
def new_account(monkeypatch):
    user_id = f"supabase-alert-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: {"sub": user_id, "email": "new@example.com"})
    return {"Authorization": "Bearer fake"}


def test_first_workspace_sends_one_alert(http, new_account, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "send_team_alert", sent.append)
    created = http.post("/clients/me", headers=new_account)
    assert created.status_code == 200
    http.post("/clients/me", headers=new_account)  # an existing account: no second alert
    assert len(sent) == 1
    assert "new@example.com" in sent[0] and f"#{created.json()['client_id']}" in sent[0]


def test_no_webhook_means_no_request(monkeypatch):
    monkeypatch.delenv("TEAM_ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(alerts.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not call out"))
    assert alerts.send_team_alert("hello") is False


def test_webhook_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("TEAM_ALERT_WEBHOOK_URL", "https://hooks.example.invalid/x")
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(alerts.urllib.request, "urlopen", boom)
    assert alerts.send_team_alert("hello") is False
