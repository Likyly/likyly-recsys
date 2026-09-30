"""SyncEngine-level tests that don't fit the HTTP-API-level tests in test_data_sources.py:
checkpoint persistence across *separate* sync calls (not just across pages within one call),
and the full-sync-never-resumes-from-a-stale-cursor safety property the tombstone diff
depends on (see sync_engine.py's docstring on why)."""
import db
from sync_engine import SyncEngine


def _make_source(client_id, **overrides):
    row = db.create_data_source(
        client_id=client_id, name="engine-test", type_="rest_api", product_type="engine-cat",
        config={"base_url": "https://x.invalid", "items_path": "items", "updated_since_param": "updated_since"},
        sync_mode="incremental",
    )
    db.update_data_source(client_id, row["id"], field_mapping={"external_id": "id", "title": "name"})
    if overrides:
        db.update_data_source(client_id, row["id"], **overrides)
    return db.get_data_source(client_id, row["id"])


def test_incremental_checkpoint_persists_across_separate_sync_calls(tenant, monkeypatch):
    captured = []

    def fake_request(self, params):
        captured.append(dict(params))

        class R:
            ok = True
            status_code = 200
            text = "{}"

            def json(self):
                return {"items": []}
        return R()

    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", fake_request)

    row = _make_source(tenant.client_id)
    SyncEngine(tenant.client_id, row).run("incremental")
    first_checkpoint = db.get_data_source(tenant.client_id, row["id"])["cursor"]
    assert first_checkpoint.get("last_updated_since")

    # A second, separate call must seed updated_since from what the first call persisted -
    # not fetch everything from scratch again.
    row = db.get_data_source(tenant.client_id, row["id"])
    SyncEngine(tenant.client_id, row).run("incremental")
    assert captured[-1]["updated_since"] == first_checkpoint["last_updated_since"]


def test_full_sync_never_resumes_from_a_stale_cursor(tenant, monkeypatch):
    """A full sync is the tombstone diff's authoritative snapshot - resuming it mid-way
    across separate calls would make it wrongly delete everything before the resume point
    (see sync_engine.SyncEngine.run's docstring). It must always restart from nothing."""
    seen_checkpoints = []

    def fake_request(self, params):
        class R:
            ok = True
            status_code = 200
            text = "{}"
            links: dict = {}

            def json(self):
                return {"items": [{"id": "x", "name": "X"}]}
        return R()

    monkeypatch.setattr("connectors.rest_api.RestApiConnector._request", fake_request)

    def spying_full_sync(self, checkpoint):
        seen_checkpoints.append(checkpoint)
        yield from RestApiConnector_full_sync(self, checkpoint)

    from connectors.rest_api import RestApiConnector
    RestApiConnector_full_sync = RestApiConnector.full_sync
    monkeypatch.setattr(RestApiConnector, "full_sync", spying_full_sync)

    row = _make_source(tenant.client_id, sync_mode="full", cursor={"page": 7})  # simulate a stale cursor left over
    SyncEngine(tenant.client_id, row).run("full")
    assert seen_checkpoints == [None]
