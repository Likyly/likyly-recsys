"""The language of the messages the API words for people: French unless the caller's
Accept-Language prefers English (or names neither French nor English)."""
import pytest

from i18n import language_from_header


@pytest.mark.parametrize("header, language", [
    (None, "fr"),  # no header: French, as before
    ("", "fr"),
    ("*", "fr"),
    ("fr", "fr"),
    ("fr-FR,fr;q=0.9,en;q=0.8", "fr"),
    ("en", "en"),
    ("en-US,en;q=0.9", "en"),
    ("en-US,en;q=0.9,fr;q=0.8", "en"),
    ("fr;q=0.5, en;q=0.8", "en"),  # by preference, not by order
    ("en;q=0, fr", "fr"),  # q=0: not acceptable
    ("de-DE,de;q=0.9", "en"),  # neither: it doesn't prefer French
    ("de-DE,fr;q=0.8,en;q=0.5", "fr"),  # the first of the two it lists
    ("FR-ca", "fr"),
    ("en;q=abc, fr;q=0.1", "fr"),  # a broken q is treated as 0
])
def test_language_from_accept_language(header, language):
    assert language_from_header(header) == language


@pytest.fixture
def console(api):
    """A dashboard session on a fresh workspace."""
    import uuid

    import db
    uid = f"supabase-{uuid.uuid4().hex[:10]}"
    client_id, _secret, _public = db.create_client_for_supabase_user("li_0123456789abcdef", uid, f"{uid}@shop.example")
    api.app.dependency_overrides[api.get_current_supabase_user_id] = lambda: uid
    yield client_id
    api.app.dependency_overrides.pop(api.get_current_supabase_user_id, None)


def test_default_event_type_labels_follow_the_language(http, console):
    http.patch("/clients/me/event-types/view", json={"label": "Page vue"})  # renamed by the tenant: kept as written
    french = {e["event_type"]: e["label"] for e in http.get("/clients/me/event-types").json()}
    assert french["purchase"] == "Achat" and french["add_to_cart"] == "Ajout au panier"
    english = {e["event_type"]: e["label"] for e in http.get("/clients/me/event-types", headers={"Accept-Language": "en"}).json()}
    assert english["purchase"] == "Purchase" and english["add_to_cart"] == "Add to cart" and english["view"] == "Page vue"


def test_auto_retrain_skip_reason_follows_the_language(http, console, api):
    import db
    api._auto_retrain_skips[(console, "shoes")] = {
        "skipped_at": db.utcnow(), "limit": 1, "waiting": 60,
        "reason": "Free plan limit reached: 1 automatic retrain(s) per day. 60 new interactions are waiting - "
                  "the model will catch up on tomorrow's automatic retrain, or upgrade your plan.",
    }
    try:
        french = http.get("/clients/me/models/status", params={"data_product_type": "shoes"}).json()
        assert french["auto_retrain_skip_reason"].startswith("Limite du plan gratuit atteinte : 1 réentraînement")
        english = http.get("/clients/me/models/status", params={"data_product_type": "shoes"}, headers={"Accept-Language": "en"}).json()
        assert english["auto_retrain_skip_reason"].startswith("Free plan limit reached: 1 automatic")
    finally:
        api._auto_retrain_skips.pop((console, "shoes"), None)
