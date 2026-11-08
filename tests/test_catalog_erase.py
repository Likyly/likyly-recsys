"""POST /catalog/erase: a whole catalog's data gone (Shopify shop/redact, GDPR store erasure)."""
from conftest import Q, interactions_of
import db


def recommendations_of(tenant):
    with db.SessionLocal() as session:
        return session.query(db.RecommendationModel).filter_by(client_id=tenant.client_id).all()


def test_erases_items_users_events_and_served_recommendations(http, seeded, other_tenant):
    http.put("/users/user_1", params=Q, headers=seeded.secret, json={"properties": {"email": "a@example.com"}})
    http.post("/events/view", params=Q, headers=seeded.public, json={"user_id": "user_1", "item_id": "SKU-NIKE-001"})
    http.post("/events/purchase", params=Q, headers=seeded.public, json={"session_id": "sess_1", "item_id": "SKU-VANS-004"})
    assert http.post("/getRec", params=Q, headers=seeded.public, json={"user_id": "user_1", "count": 3}).status_code == 200
    # Another account's data is never touched.
    http.put("/items/OTHER-1", params=Q, headers=other_tenant.secret, json={"title": "Other"})
    http.post("/events/view", params=Q, headers=other_tenant.public, json={"user_id": "x", "item_id": "OTHER-1"})

    response = http.post("/catalog/erase", params=Q, headers=seeded.secret, json={"confirm": "shop"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["catalog"] == "shop" and body["items"] == 8 and body["users"] == 1 and body["events"] == 2

    assert http.get("/items", params=Q, headers=seeded.secret).json() == []
    assert http.get("/users/user_1", params=Q, headers=seeded.secret).status_code == 404
    assert interactions_of(seeded) == []
    assert recommendations_of(seeded) == []
    assert len(interactions_of(other_tenant)) == 1
    assert len(http.get("/items", params=Q, headers=other_tenant.secret).json()) == 1


def test_needs_the_catalog_name_and_the_secret_key(http, seeded):
    assert http.post("/catalog/erase", params=Q, headers=seeded.secret, json={"confirm": "other"}).status_code == 422
    assert http.post("/catalog/erase", params=Q, headers=seeded.public, json={"confirm": "shop"}).status_code in (401, 403)
    assert len(http.get("/items", params=Q, headers=seeded.secret).json()) == 8


def test_an_empty_catalog_answers_zeros(http, tenant):
    response = http.post("/catalog/erase", params=Q, headers=tenant.secret, json={"confirm": "shop"})
    assert response.status_code == 200
    assert response.json() == {"catalog": "shop", "items": 0, "users": 0, "events": 0, "recommendations": 0}
