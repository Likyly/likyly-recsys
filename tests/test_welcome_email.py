"""Welcome email: sent once, when an account's first workspace is created, whatever the sign-in
method (Supabase sends nothing to a Google/GitHub sign-up). Never calls a real SMTP server."""
import pytest

import app as app_module
import welcome

USER_ID = "supabase-user-welcome"


@pytest.fixture
def sent(monkeypatch):
    outbox = []
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "hello@likyly.com")
    monkeypatch.setattr(welcome, "deliver", lambda message, settings: outbox.append(message))
    return outbox


def _sign_in(monkeypatch, sub, email, locale=None):
    claims = {"sub": sub, "email": email}
    if locale:
        claims["user_metadata"] = {"locale": locale}
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: claims)
    return {"Authorization": "Bearer fake"}


def test_first_workspace_sends_one_welcome_in_french(http, monkeypatch, sent):
    headers = _sign_in(monkeypatch, USER_ID, "new@example.com")
    assert http.post("/clients/me", headers=headers).status_code == 200
    assert http.post("/clients/me", headers=headers).status_code == 200  # later calls: no second mail

    assert len(sent) == 1
    message = sent[0]
    assert message["To"] == "new@example.com"
    assert message["Subject"] == "Bienvenue sur Likyly"
    html = message.get_body(("html",)).get_content()
    assert "npx @likyly/wizard" in html
    assert "https://www.likyly.com/docs/installation" in html
    assert "{{" not in html  # every placeholder filled
    assert "npx @likyly/wizard" in message.get_body(("plain",)).get_content()


def test_welcome_follows_the_account_language(http, monkeypatch, sent):
    headers = _sign_in(monkeypatch, USER_ID + "-en", "en@example.com", locale="en")
    http.post("/clients/me", headers=headers)
    assert sent[0]["Subject"] == "Welcome to Likyly"


def test_sign_up_works_without_smtp(http, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    headers = _sign_in(monkeypatch, USER_ID + "-nosmtp", "nosmtp@example.com")
    assert http.post("/clients/me", headers=headers).status_code == 200
    assert welcome.send_welcome("nosmtp@example.com", None) is False
