"""Sample traffic for the demo workspace (demo@likyly.com, used by the Claude and ChatGPT directory
reviewers): visitors on book pages get the "You may also like" or "Recommended for you" block, see
it, sometimes click, add to cart and buy - so the analytics tools have recent figures to report.

Runs inside the API container, through the API itself (same path as a real site), with the
workspace's "Demo site" public key - created on the first run and then kept, never revoked:
revoking it would make the placement health report "no call since a key was revoked".

    docker exec -e PYTHONPATH=/srv/application/utils:/srv/application/api recsys-api \
        python /srv/scripts/demo_traffic.py [visits]

Only for the demo workspace (DEMO_CLIENT_ID): it refuses any other one.
"""
import random
import sys
import uuid

import httpx

import db

DEMO_CLIENT_ID = 11
DEMO_WORKSPACE = "li_fa6bb43c557f4dd3"
KEY_NAME = "Demo site"
API = "http://127.0.0.1:6061"
PRICES = {"BK-001": 14.9, "BK-002": 11.5, "BK-003": 9.9, "BK-004": 12.9, "BK-005": 16.5, "BK-006": 15.9, "BK-007": 14.5,
          "BK-008": 13.9, "BK-009": 12.5, "BK-010": 17.9, "BK-011": 10.9, "BK-012": 13.5, "BK-013": 16.9, "BK-014": 18.5,
          "BK-015": 14.9, "BK-016": 12.9, "BK-017": 17.5, "BK-018": 15.5, "BK-019": 18.9, "BK-020": 13.5}


def demo_key() -> str:
    for key in db.list_site_public_keys(DEMO_CLIENT_ID):
        if key["name"] == KEY_NAME and key["public_key"]:
            return key["public_key"]
    return db.create_site_public_key(DEMO_CLIENT_ID, KEY_NAME)[1]


def run(visits: int) -> dict:
    counts = {"visits": 0, "served": 0, "impressions": 0, "clicks": 0, "carts": 0, "purchases": 0, "errors": 0}
    with httpx.Client(base_url=API, headers={"X-API-Key": demo_key()}, timeout=30) as client:
        def event(kind: str, **body) -> None:
            if client.post(f"/events/{kind}", json=body).status_code >= 300:
                counts["errors"] += 1

        for v in range(visits):
            session = f"demo-sess-{uuid.uuid4().hex[:10]}"
            user = f"demo-user-{random.randrange(25)}" if random.random() < 0.33 else None
            who = {"session_id": session, **({"user_id": user} if user else {})}
            page = random.choice(list(PRICES))
            counts["visits"] += 1
            event("product_view", item_id=page, **who)
            slug, ctx = ("home-for-you", {}) if v % 5 == 0 else ("pdp-you-may-also-like", {"current_item_id": page})
            response = client.post(f"/placements/{slug}/recommend", json={"context": {**ctx, **who}})
            if response.status_code != 200:
                counts["errors"] += 1
                continue
            body = response.json()
            rec_id = body.get("recommendation_id")
            items = [i.get("item_id") for i in body.get("items", []) if i.get("item_id")]
            counts["served"] += 1
            for item in items:
                event("recommendation_impression", item_id=item, recommendation_id=rec_id, placement=slug, **who)
                counts["impressions"] += 1
            if not items or random.random() >= 0.35:
                continue
            clicked = random.choice(items[:3])
            event("recommendation_click", item_id=clicked, recommendation_id=rec_id, placement=slug, **who)
            event("product_view", item_id=clicked, recommendation_id=rec_id, **who)
            counts["clicks"] += 1
            if random.random() < 0.6:
                event("add_to_cart", item_id=clicked, recommendation_id=rec_id, **who)
                counts["carts"] += 1
                if random.random() < 0.7:
                    event("purchase", item_id=clicked, recommendation_id=rec_id, quantity=1,
                          event_id=f"demo-order-{uuid.uuid4().hex[:12]}",
                          properties={"price": PRICES.get(clicked, 14.9), "currency": "USD"}, **who)
                    counts["purchases"] += 1
    return counts


def is_demo_workspace() -> bool:
    with db.SessionLocal() as session:
        client = session.get(db.ClientModel, DEMO_CLIENT_ID)
        return client is not None and client.name == DEMO_WORKSPACE


if __name__ == "__main__":
    if not is_demo_workspace():
        sys.exit(f"client {DEMO_CLIENT_ID} is not the demo workspace {DEMO_WORKSPACE}")
    print(run(int(sys.argv[1]) if len(sys.argv) > 1 else 12))
