"""Retention: visitor data older than 13 months is purged (retention.py), the rest stays."""
from sqlalchemy import text

from conftest import Q
import db
import retention

OLD = "now() - interval '14 months'"
RECENT = "now() - interval '12 months'"


def _sql(statement: str, **params):
    with db.engine.begin() as conn:
        return conn.execute(text(statement), params)


def _user_ids(tenant) -> set[str]:
    rows = _sql("SELECT external_id FROM id_map WHERE client_id = :c AND kind = 'user'", c=tenant.client_id)
    return {row[0] for row in rows}


def _events(tenant) -> list[tuple]:
    rows = _sql("SELECT session_id, event_type FROM interactions WHERE client_id = :c ORDER BY id", c=tenant.client_id)
    return [tuple(row) for row in rows]


def test_purges_what_is_older_than_13_months_and_keeps_the_rest(http, seeded, other_tenant):
    for user in ("gone", "back", "new"):
        http.put(f"/users/{user}", params=Q, headers=seeded.secret, json={"properties": {"segment": "x"}})
    view = lambda body: http.post("/events/view", params=Q, headers=seeded.public, json={"item_id": "SKU-NIKE-001", **body})
    view({"user_id": "gone", "session_id": "s_old"})
    view({"user_id": "back", "session_id": "s_old"})
    view({"user_id": "back", "session_id": "s_recent"})
    view({"session_id": "s_anonymous_old"})
    http.put("/items/OTHER-1", params=Q, headers=other_tenant.secret, json={"title": "Other"})
    http.post("/events/view", params=Q, headers=other_tenant.public, json={"session_id": "s_other_old", "item_id": "OTHER-1"})
    assert http.post("/getRec", params=Q, headers=seeded.public, json={"session_id": "s_old", "count": 3}).status_code == 200
    assert http.post("/getRec", params=Q, headers=seeded.public, json={"session_id": "s_recent", "count": 3}).status_code == 200

    # Age the data: every event but the recent one, the first trace, and the mappings of
    # "gone" and "back" ("new" was just created and has no event yet: it stays).
    _sql(f"UPDATE interactions SET occurred_at = {OLD} WHERE session_id IN ('s_old', 's_anonymous_old', 's_other_old')")
    _sql(f"UPDATE interactions SET occurred_at = {RECENT} WHERE session_id = 's_recent'")
    _sql(f"UPDATE recommendations SET created_at = {OLD} WHERE client_id = :c AND session_id = 's_old'", c=seeded.client_id)
    _sql(f"UPDATE id_map SET created_at = {OLD} WHERE client_id = :c AND kind = 'user' AND external_id IN ('gone', 'back')", c=seeded.client_id)

    assert retention.count_expired() == {"interactions": 4, "recommendations": 1, "users": 1}
    assert retention.purge_expired_data() == {"interactions": 4, "recommendations": 1, "users": 1}

    assert _events(seeded) == [("s_recent", "view")]
    assert _events(other_tenant) == []
    assert _user_ids(seeded) == {"back", "new"}
    assert http.get("/users/gone", params=Q, headers=seeded.secret).status_code == 404
    assert http.get("/users/back", params=Q, headers=seeded.secret).status_code == 200
    sessions = _sql("SELECT session_id FROM recommendations WHERE client_id = :c", c=seeded.client_id)
    assert [row[0] for row in sessions] == ["s_recent"]

    # Nothing left to do: a second run deletes nothing.
    assert retention.purge_expired_data() == {"interactions": 0, "recommendations": 0, "users": 0}


def test_deletes_in_batches(http, seeded, monkeypatch):
    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    for n in range(5):
        http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": f"batch_{n}", "item_id": "SKU-NIKE-001"})
    _sql(f"UPDATE interactions SET occurred_at = {OLD} WHERE session_id LIKE 'batch_%'")
    assert retention.purge_expired_data()["interactions"] == 5
    assert _events(seeded) == []


# --- Runs and the back-office --------------------------------------------------------------------

def _as_admin(monkeypatch, is_admin=True):
    import app as app_module
    claims = {"sub": "admin-user", "email": "admin@example.com", "aal": "aal2", "app_metadata": {"is_admin": is_admin}}
    monkeypatch.setattr(app_module, "_decode_supabase_jwt", lambda authorization: claims)
    return {"Authorization": "Bearer fake"}


def test_a_run_is_recorded_with_what_it_deleted(http, seeded):
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "run_old", "item_id": "SKU-NIKE-001"})
    _sql(f"UPDATE interactions SET occurred_at = {OLD} WHERE session_id = 'run_old'")
    run = retention.run_now(retention.DAILY)
    assert (run["trigger"], run["status"], run["interactions"], run["error"]) == ("daily", "succeeded", 1, None)
    assert run["finished_at"] is not None
    assert retention.list_runs()[0]["id"] == run["id"]
    # The next daily run is a day after this one.
    assert not retention._daily_due()


def test_a_failed_run_says_so_and_frees_the_lock(monkeypatch):
    def broken():
        raise RuntimeError("database gone")
    monkeypatch.setattr(retention, "purge_expired_data", broken)
    run = retention.run_now()
    assert (run["status"], run["error"], run["interactions"]) == ("failed", "RuntimeError", None)
    monkeypatch.undo()
    assert retention.run_now()["status"] == "succeeded"  # the lock was released


def test_admin_sees_the_rule_and_the_runs_and_starts_one(http, seeded, monkeypatch):
    headers = _as_admin(monkeypatch)
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "admin_old", "item_id": "SKU-NIKE-001"})
    _sql(f"UPDATE interactions SET occurred_at = {OLD} WHERE session_id = 'admin_old'")

    before = http.get("/admin/retention", headers=headers)
    assert before.status_code == 200, before.text
    body = before.json()
    assert body["retention_months"] == 13 and body["daily_enabled"] is True
    assert body["expired_now"]["interactions"] == 1

    started = http.post("/admin/retention/run", headers=headers)
    assert started.status_code == 202, started.text
    assert (started.json()["trigger"], started.json()["triggered_by"]) == ("manual", "admin-user")

    after = http.get("/admin/retention", headers=headers).json()
    assert after["expired_now"]["interactions"] == 0
    latest = after["runs"][0]
    assert (latest["id"], latest["status"], latest["interactions"]) == (started.json()["id"], "succeeded", 1)
    assert after["last_success_at"] is not None and after["next_run_at"] > after["last_success_at"]


def test_one_purge_at_a_time(http, monkeypatch):
    headers = _as_admin(monkeypatch)
    run = retention.start_run(retention.MANUAL, "someone")
    try:
        assert http.post("/admin/retention/run", headers=headers).status_code == 409
    finally:
        retention.execute_run(run["id"])
    assert http.post("/admin/retention/run", headers=headers).status_code == 202


def test_daily_runs_can_be_turned_off(http, monkeypatch):
    headers = _as_admin(monkeypatch)
    monkeypatch.setenv("LIKYLY_RETENTION_PURGE", "off")
    body = http.get("/admin/retention", headers=headers).json()
    assert body["daily_enabled"] is False and body["next_run_at"] is None


def test_admin_only(http, monkeypatch):
    headers = _as_admin(monkeypatch, is_admin=False)
    assert http.get("/admin/retention", headers=headers).status_code == 403
    assert http.post("/admin/retention/run", headers=headers).status_code == 403
