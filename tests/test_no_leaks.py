"""Nothing internal or personal reaches a caller who shouldn't see it."""
import json
import re


import db
from conftest import Q

PATH_LIKE = re.compile(r"(/srv/|/Users/|/home/|\.npz|site-packages|Traceback|psycopg|sqlalchemy|SELECT |INSERT )", re.I)


def test_public_key_cannot_export_the_catalog(http, seeded):
    assert http.get("/items", params=Q, headers=seeded.public).status_code == 403
    assert http.get("/items/SKU-NIKE-001", params=Q, headers=seeded.public).status_code == 403


def test_public_recommendations_expose_only_recommended_items(http, seeded):
    body = http.post("/getRec", params=Q, headers=seeded.public, json={"count": 2}).json()
    assert len(body["items"]) == 2  # two items, never the catalog


def test_missing_model_error_does_not_reveal_server_paths(http, seeded):
    http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "u", "item_id": "SKU-NIKE-001"})
    for headers in (seeded.public, seeded.secret):
        response = http.get("/getRec/collaborative/u/3", params=Q, headers=headers)
        assert response.status_code == 404 and not PATH_LIKE.search(response.text)


def test_collaborative_failure_is_generic(http, seeded, api, monkeypatch):
    http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": "u", "item_id": "SKU-NIKE-001"})
    monkeypatch.setattr(api, "rec_collaborative", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret /srv/model/client_9.npz password=hunter2")))
    response = http.get("/getRec/collaborative/u/3", params=Q, headers=seeded.public)
    assert response.status_code == 500 and "hunter2" not in response.text and not PATH_LIKE.search(response.text)


def test_failed_training_job_status_is_generic(http, seeded, api, monkeypatch):
    monkeypatch.setattr(api, "train_and_maybe_promote_model", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("could not connect to server at /srv/secret.sock password=hunter2")))
    job = http.get("/generateModel", params=Q, headers=seeded.secret).json()
    status = http.get(f"/generateModel/status/{job['job_id']}", headers=seeded.secret).json()
    assert status["status"] == "failed" and "hunter2" not in json.dumps(status) and not PATH_LIKE.search(json.dumps(status))


def test_model_versions_do_not_expose_the_artifact_path(http, seeded, tmp_path, monkeypatch):
    import modelData
    monkeypatch.setattr(modelData, "dvc_push", lambda *a, **k: None)
    monkeypatch.setattr(modelData, "_log_training_run_to_mlflow", lambda **k: None)
    monkeypatch.setattr(modelData, "_versioned_model_path", lambda t, c, v: str(tmp_path / f"m{v}.npz"))
    for i in range(6):
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": f"u{i}", "item_id": ["SKU-NIKE-001", "SKU-VANS-004"][i % 2]})
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": f"u{i}", "item_id": "SKU-ASICS-003"})
    modelData.train_and_maybe_promote_model(Q["data_product_type"], client_id=seeded.client_id)
    versions = http.get("/models/versions", params=Q, headers=seeded.secret)
    status = http.get("/models/status", params=Q, headers=seeded.secret)
    assert versions.json() and "file_path" not in versions.text and "file_path" not in status.text
    assert str(tmp_path) not in versions.text + status.text


def test_csv_row_errors_never_echo_database_internals(api):
    from sqlalchemy.exc import IntegrityError
    leaked = IntegrityError("INSERT INTO products (...) VALUES (%(secret)s)", {"secret": "hunter2"}, Exception("duplicate key"))
    message = api._row_error_message(leaked)
    assert message == "Unexpected error while importing this row"
    assert api._row_error_message(ValueError("title is required")) == "title is required"


def test_unhandled_errors_never_leak_exception_text(http, seeded, api, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(api, "recommend_auto", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("password=hunter2 /srv/x")))
    body = TestClient(api.app, raise_server_exceptions=False).post("/getRec", params=Q, headers=seeded.public, json={}).text
    assert "hunter2" not in body and "/srv/" not in body


def test_user_data_is_not_readable_with_a_public_key_by_any_route(http, seeded):
    http.put("/users/jane", params=Q, headers=seeded.secret, json={"properties": {"email": "jane@example.com"}})
    for method, path in [("GET", "/users"), ("GET", "/users/jane"), ("DELETE", "/users/jane")]:
        response = http.request(method, path, params=Q, headers=seeded.public)
        assert response.status_code == 403 and "jane@example.com" not in response.text


def test_recommendations_do_not_carry_other_users_events_or_ids(http, seeded):
    for user in ("alice", "bob"):
        http.post("/events/purchase", params=Q, headers=seeded.public, json={"user_id": user, "item_id": "SKU-NIKE-001"})
    body = http.post("/getRec", params=Q, headers=seeded.public, json={"user_id": "alice", "count": 5}).text
    assert "bob" not in body and "alice" not in body


def test_error_bodies_are_uniform_and_carry_no_stack_information(http, seeded):
    for response in (http.get("/items/nope", params=Q, headers=seeded.secret), http.get("/nonexistent"), http.post("/getRec", params=Q, json={})):
        assert set(response.json()) == {"detail", "request_id"} and not PATH_LIKE.search(response.text)


def test_a_key_of_one_tenant_never_works_as_another(http, seeded, other_tenant):
    http.put("/items/mine", params=Q, headers=seeded.secret, json={"title": "t"})
    assert http.get("/items/mine", params=Q, headers=other_tenant.secret).status_code == 404
    assert db.get_client_and_scope_by_api_key(other_tenant.public_key) == (other_tenant.client_id, "public")


def test_no_credentials_in_the_repository_sources():
    """Static check on tracked source: no API-key-looking literals or DB URLs with passwords."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "application" / "api").glob("*.py")) + list((root / "application" / "utils").glob("*.py")) + list((root / "sdk").glob("*/src/**/*")) + list((root / "gateway").glob("*.sh")):
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        if re.search(r"postgres(ql)?://[^\s:'\"@]+:[^\s'\"@]{3,}@", text) or re.search(r"apiKey:\s*[\"'][A-Za-z0-9_-]{30,}[\"']", text):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
