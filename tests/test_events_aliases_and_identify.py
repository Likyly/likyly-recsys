"""Event vocabulary aliases (product_view/recommendation_impression/recommendation_click ->
view/impression/click, for training-weight continuity) and POST /events/identify (session ->
user stitching, application/utils/db.py's link_session_to_user)."""
import db
from conftest import Q


def test_alias_lands_on_the_canonical_event_type_and_weight(http, seeded):
    response = http.post("/events/recommendation_impression", params=Q, headers=seeded.public, json={
        "session_id": "s1", "item_id": "SKU-NIKE-001",
    })
    assert response.status_code == 200, response.text

    row = db.InteractionModel
    with db.SessionLocal() as session:
        interaction = session.query(row).filter_by(client_id=seeded.client_id, event_type="impression").first()
        assert interaction is not None
        aliased = session.query(row).filter_by(client_id=seeded.client_id, event_type="recommendation_impression").first()
        assert aliased is None  # never stored under the alias name


def test_product_view_and_recommendation_click_aliases(http, seeded):
    http.post("/events/product_view", params=Q, headers=seeded.public, json={"session_id": "s1", "item_id": "SKU-NIKE-001"})
    http.post("/events/recommendation_click", params=Q, headers=seeded.public, json={"session_id": "s1", "item_id": "SKU-NIKE-001"})
    with db.SessionLocal() as session:
        assert session.query(db.InteractionModel).filter_by(client_id=seeded.client_id, event_type="view").count() == 1
        assert session.query(db.InteractionModel).filter_by(client_id=seeded.client_id, event_type="click").count() == 1


def test_batch_events_are_also_aliased(http, seeded):
    response = http.post("/events/batch", params=Q, headers=seeded.public, json={"events": [
        {"event_type": "recommendation_impression", "session_id": "s1", "item_id": "SKU-NIKE-001"},
        {"event_type": "product_view", "session_id": "s1", "item_id": "SKU-ADIDAS-007"},
    ]})
    assert response.status_code == 200, response.text
    with db.SessionLocal() as session:
        assert session.query(db.InteractionModel).filter_by(client_id=seeded.client_id, event_type="impression").count() == 1
        assert session.query(db.InteractionModel).filter_by(client_id=seeded.client_id, event_type="view").count() == 1


def test_identify_links_past_anonymous_interactions(http, seeded):
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_1", "item_id": "SKU-NIKE-001"})
    http.post("/events/click", params=Q, headers=seeded.public, json={"session_id": "sess_1", "item_id": "SKU-ADIDAS-007"})

    response = http.post("/events/identify", params=Q, headers=seeded.public, json={"user_id": "user_123", "session_id": "sess_1"})
    assert response.status_code == 200, response.text
    assert response.json()["linked_interactions"] == 2

    internal_user = db.resolve_internal_ids(seeded.client_id, "shop", db.KIND_USER, ["user_123"])["user_123"]
    with db.SessionLocal() as session:
        linked = session.query(db.InteractionModel).filter_by(client_id=seeded.client_id, session_id="sess_1", user_id=internal_user).count()
        assert linked == 2


def test_identify_never_reassigns_already_attributed_interactions(http, seeded):
    """A session that already has a different user's history (e.g. a shared device) must not
    have that history silently reassigned - only unattributed rows are touched."""
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_shared", "user_id": "user_a", "item_id": "SKU-NIKE-001"})
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_shared", "item_id": "SKU-ADIDAS-007"})  # anonymous-only row

    response = http.post("/events/identify", params=Q, headers=seeded.public, json={"user_id": "user_b", "session_id": "sess_shared"})
    assert response.json()["linked_interactions"] == 1  # only the anonymous-only row

    internal_a = db.resolve_internal_ids(seeded.client_id, "shop", db.KIND_USER, ["user_a"])["user_a"]
    with db.SessionLocal() as session:
        still_a = session.query(db.InteractionModel).filter_by(client_id=seeded.client_id, session_id="sess_shared", user_id=internal_a).count()
        assert still_a == 1


def test_identify_is_idempotent(http, seeded):
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_2", "item_id": "SKU-NIKE-001"})
    first = http.post("/events/identify", params=Q, headers=seeded.public, json={"user_id": "user_x", "session_id": "sess_2"})
    second = http.post("/events/identify", params=Q, headers=seeded.public, json={"user_id": "user_x", "session_id": "sess_2"})
    assert first.json()["linked_interactions"] == 1
    assert second.json()["linked_interactions"] == 0  # already attributed - nothing left to link


def test_identify_is_tenant_isolated(http, seeded, other_tenant):
    http.post("/events/view", params=Q, headers=seeded.public, json={"session_id": "sess_3", "item_id": "SKU-NIKE-001"})
    response = http.post("/events/identify", params={"data_product_type": "shop"}, headers=other_tenant.public, json={"user_id": "user_y", "session_id": "sess_3"})
    assert response.json()["linked_interactions"] == 0  # other_tenant has no such session
