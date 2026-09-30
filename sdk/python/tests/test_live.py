"""End-to-end against a REAL LIKYLY API (sdk/conformance/local-api.sh starts one). Skipped unless LIKYLY_TEST_* are set."""
import time

import pytest
from conftest import LIVE, live

import likyly

GID = "gid://shopify/Product/123456"


@pytest.fixture
def catalog():
    return f"py-{int(time.time() * 1000)}"


@live
def test_full_workflow(catalog):
    server = likyly.Likyly(LIVE["LIKYLY_TEST_SECRET_KEY"], base_url=LIVE["LIKYLY_TEST_URL"], catalog=catalog)
    browser = likyly.Likyly(LIVE["LIKYLY_TEST_PUBLIC_KEY"], base_url=LIVE["LIKYLY_TEST_URL"], catalog=catalog)

    # items, ids of any shape
    created = server.items.upsert("SKU-123", title="Nike Air Max", description="Running shoe", properties={"category": "shoes", "price": 129.9})
    assert created.item_id == "SKU-123" and created.properties["price"] == 129.9
    server.items.upsert(GID, title="Shopify boot", description="warm winter boot", properties={"category": "boots"})
    assert server.items.get(GID).title == "Shopify boot"
    page = server.items.list(limit=1)
    assert len(page.items) == 1 and page.total == 2
    assert server.items.upsert_many([{"item_id": "SKU-A", "title": "Adidas", "description": "road running shoe"}]).succeeded == 1
    server.items.delete("SKU-A")
    with pytest.raises(likyly.NotFoundError):
        server.items.get("SKU-A")

    # users
    assert server.users.upsert("user_123", properties={"country": "FR", "segment": "premium"}).properties["segment"] == "premium"
    assert any(u.user_id == "user_123" for u in server.users.list(limit=10).users)

    # events with the PUBLIC key: identified, anonymous, custom, idempotent purchase
    browser.events.view(user_id="user_123", item_id="SKU-123")
    browser.events.view(session_id="sess_123", item_id="SKU-123")
    browser.events.track("favorite", user_id="user_123", item_id="SKU-123")
    purchase = dict(event_id=f"purchase_{time.time_ns()}", user_id="user_123", item_id="SKU-123", properties={"orderId": "O-1"})
    assert browser.events.purchase(**purchase).duplicate is False
    assert browser.events.purchase(**purchase).duplicate is True
    assert browser.events.track_many([{"type": "view", "user_id": "user_123", "item_id": GID}]).accepted == 1

    # recommendations + attribution
    rec = browser.recommendations.get(user_id="user_123", placement="homepage", limit=2)
    assert rec.recommendation_id.startswith("rec_") and rec.placement == "homepage" and rec.items
    assert browser.recommendations.get(item_id="SKU-123", limit=2).strategy == "content"
    assert browser.recommendations.get(item_id=GID, limit=2).strategy == "content"
    browser.events.impression(user_id="user_123", item_id=rec.items[0].item_id, recommendation_id=rec.recommendation_id, placement="homepage")
    assert browser.recommendations.similar(item_id="SKU-123", limit=2).strategy == "content"
    assert browser.recommendations.session(viewed_item_ids=["SKU-123"], limit=2).strategy == "session"
    assert browser.recommendations.popular(limit=2).recommendation_id

    # permissions and validation
    with pytest.raises(likyly.PermissionDeniedError):
        browser.items.upsert("x", title="t")
    with pytest.raises(likyly.AuthenticationError):
        likyly.Likyly("nope", base_url=LIVE["LIKYLY_TEST_URL"]).events.view(user_id="u", item_id="i")
    with pytest.raises(likyly.ValidationError) as info:
        server.items.list(limit=5000)
    assert (info.value.request_id or "").startswith("req_")

    server.items.delete_many(["SKU-123", GID])
    server.users.delete("user_123")


@live
async def test_async_client_end_to_end(catalog):
    async with likyly.AsyncLikyly(LIVE["LIKYLY_TEST_SECRET_KEY"], base_url=LIVE["LIKYLY_TEST_URL"], catalog=catalog) as client:
        await client.items.upsert("a", title="Red shoe", description="running shoe")
        await client.items.upsert("b", title="Blue shoe", description="running shoe")
        rec = await client.recommendations.get(item_id="a", limit=2)
        assert rec.items[0].item_id == "b"
        await client.items.delete_many(["a", "b"])
