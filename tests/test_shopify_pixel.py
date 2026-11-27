"""The Shopify custom pixel's hosted script (/pixel/shopify.js): served as JavaScript, and run in
node against a fake Shopify pixel sandbox - queued events are not lost, a purchase is one event
per product with the visitor and the clicked recommendation."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "application" / "api" / "static" / "shopify-pixel.js"

HARNESS = r"""
const sent = [];
globalThis.fetch = (url, opts) => { sent.push({ url, headers: opts.headers, body: JSON.parse(opts.body) }); return Promise.resolve({}); };
const store = {
  likyly_anonymous_id: "anon_1",
  likyly_rec: JSON.stringify({ "101": ["rec_1", "pdp", Date.now()], "102": ["rec_old", "pdp", 0] }),
};
const browser = { localStorage: { getItem: (k) => Promise.resolve(store[k] ?? null) } };
const init = { data: { customer: { id: "gid://shopify/Customer/77" } } };
const subscribed = {};
const analytics = { subscribe: (n, f) => { subscribed[n] = f; } };
// What the merchant pastes: subscribe first, queue, load the file later.
const q = [];
analytics.subscribe("product_added_to_cart", (e) => q.push(e));
analytics.subscribe("checkout_completed", (e) => q.push(e));
const checkout = { name: "checkout_completed", id: "e1", timestamp: "2026-11-27T10:00:00Z", data: { checkout: {
  order: { id: "gid://shopify/Order/555" }, currencyCode: "EUR",
  lineItems: [
    { quantity: 2, finalLinePrice: { amount: 200 }, variant: { price: { amount: 100 }, product: { id: "gid://shopify/Product/101" } } },
    { quantity: 1, variant: { price: { amount: 50 }, product: { id: "gid://shopify/Product/102" } } },
  ] } } };
subscribed.checkout_completed(checkout); // before the file arrives
require(process.argv[2]);
globalThis.Likyly.pixel("pk_test", { analytics, browser, init, queue: q, api: "https://api.test" });
subscribed.product_added_to_cart({ name: "product_added_to_cart", id: "e2", timestamp: "t", data: { cartLine: {
  quantity: 1, merchandise: { product: { id: "gid://shopify/Product/101" }, price: { amount: 100, currencyCode: "EUR" } } } } });
setTimeout(() => console.log(JSON.stringify(sent)), 50);
"""


def test_served_as_javascript(http):
    response = http.get("/pixel/shopify.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "Likyly.pixel" in response.text and "max-age" in response.headers["cache-control"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_queued_purchase_and_later_add_to_cart(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS)
    out = subprocess.run(["node", str(harness), str(SCRIPT)], capture_output=True, text=True, timeout=20, check=True).stdout
    purchase_call, cart_call = json.loads(out)
    assert purchase_call["url"] == "https://api.test/events/batch"
    assert purchase_call["headers"]["X-API-Key"] == "pk_test"
    first, second = purchase_call["body"]["events"]
    assert first == {
        "event_type": "purchase", "item_id": "101", "quantity": 2, "event_id": "purchase_555_101",
        "occurred_at": "2026-11-27T10:00:00Z", "session_id": "anon_1", "user_id": "77",
        "properties": {"order_id": "555", "price": 100, "revenue": 200, "currency": "EUR"},
        "recommendation_id": "rec_1", "placement": "pdp",
    }
    assert second["item_id"] == "102" and "recommendation_id" not in second  # click older than 30 days
    assert second["properties"]["revenue"] == 50
    (cart,) = cart_call["body"]["events"]
    assert cart["event_type"] == "add_to_cart" and cart["recommendation_id"] == "rec_1" and cart["event_id"] == "cart_e2"
