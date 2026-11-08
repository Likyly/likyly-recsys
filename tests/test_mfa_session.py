"""An account with two-step verification gets an aal1 session right after its password - before
the code. That session must not reach the console API: only aal2 does. Supabase's Admin API is
faked here (no network)."""
import httpx
import pytest

import app as app_module


class _Response:
    def __init__(self, factors, status=200):
        self.status_code = status
        self._factors = factors

    def json(self):
        return {"factors": self._factors}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


@pytest.fixture(autouse=True)
def admin_api(monkeypatch):
    monkeypatch.setattr(app_module, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    app_module._MFA_FACTOR_CACHE.clear()
    calls = []
    state = {"factors": [], "status": 200}

    def fake_get(url, headers, timeout):
        calls.append(url)
        return _Response(state["factors"], state["status"])

    monkeypatch.setattr(app_module.httpx, "get", fake_get)
    yield state, calls
    app_module._MFA_FACTOR_CACHE.clear()


def _session(monkeypatch, sub, aal):
    claims = {"sub": sub, "email": f"{sub}@example.com", "aal": aal}
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: claims)
    return {"Authorization": "Bearer fake"}


def test_aal1_session_of_an_mfa_account_is_refused(http, monkeypatch, admin_api):
    state, _ = admin_api
    state["factors"] = [{"id": "f1", "status": "verified", "factor_type": "totp"}]
    response = http.post("/clients/me", headers=_session(monkeypatch, "mfa-user-1", "aal1"))
    assert response.status_code == 403
    assert response.json()["detail"] == app_module.MFA_REQUIRED_DETAIL


def test_aal2_session_of_an_mfa_account_goes_through_without_lookup(http, monkeypatch, admin_api):
    state, calls = admin_api
    state["factors"] = [{"id": "f1", "status": "verified", "factor_type": "totp"}]
    assert http.post("/clients/me", headers=_session(monkeypatch, "mfa-user-2", "aal2")).status_code == 200
    assert calls == []


def test_aal1_without_verified_factor_goes_through(http, monkeypatch, admin_api):
    state, calls = admin_api
    state["factors"] = [{"id": "f1", "status": "unverified", "factor_type": "totp"}]  # enrollment not finished
    headers = _session(monkeypatch, "mfa-user-3", "aal1")
    assert http.post("/clients/me", headers=headers).status_code == 200
    assert http.post("/clients/me", headers=headers).status_code == 200
    assert len(calls) == 1  # cached


def test_factor_lookup_failure_fails_closed(http, monkeypatch, admin_api):
    state, _ = admin_api
    state["status"] = 500
    assert http.post("/clients/me", headers=_session(monkeypatch, "mfa-user-4", "aal1")).status_code == 503
