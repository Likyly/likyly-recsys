"""Test harness.

The tests run the real FastAPI app against a real Postgres (with pgvector) - never against
the database in application/api/.env, which points at production. Start a throwaway one:

    docker run -d --name likyly-test-pg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=likyly_test -p 54329:5432 pgvector/pgvector:pg16

then `pytest`. TEST_DATABASE_URL overrides the default; it must be a local database, and
its `public` schema is DROPPED at session start.
"""
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://test:test@localhost:54329/likyly_test")

if urlparse(TEST_DATABASE_URL).hostname not in {"localhost", "127.0.0.1", "::1"}:
    pytest.exit(f"Refusing to run: TEST_DATABASE_URL must point at a local database, got {urlparse(TEST_DATABASE_URL).hostname!r}", returncode=2)

# Overrides whatever application/api/.env says (python-dotenv never overrides an existing
# environment variable), so importing the app can never touch a real database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SUPABASE_URL"] = "https://example.invalid"
os.environ.pop("SENTRY_DSN", None)

sys.path[:0] = [str(ROOT / "application" / "api"), str(ROOT / "application" / "utils")]


def _reset_database(url: str) -> None:
    connection = psycopg2.connect(url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP SCHEMA IF EXISTS public CASCADE")
        cursor.execute("CREATE SCHEMA public")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
    connection.close()


_reset_database(TEST_DATABASE_URL)

import modelData  # noqa: E402  (after the environment is pinned)

# No network / model download in tests: a deterministic 384-d vector derived from the text,
# so semantically "equal" texts are identical and different ones differ.
def _fake_embedding(text: str) -> list[float]:
    import hashlib
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(384)]


modelData.compute_embedding = _fake_embedding
modelData.compute_embeddings = lambda texts: [_fake_embedding(t) for t in texts]

import app as app_module  # noqa: E402
import db  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def api():
    return app_module


@pytest.fixture(scope="session")
def http():
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _no_auto_retrain(monkeypatch):
    # Auto-retraining is a background training job - covered by its own test, off elsewhere.
    monkeypatch.setattr(app_module, "AUTO_RETRAIN_INTERACTION_THRESHOLD", 10**9)


@dataclass
class Tenant:
    client_id: int
    secret_key: str
    public_key: str

    @property
    def secret(self) -> dict:
        return {"X-API-Key": self.secret_key}

    @property
    def public(self) -> dict:
        return {"X-API-Key": self.public_key}


def make_tenant(plan: str = db.PLAN_UNLIMITED) -> Tenant:
    client_id, secret_key, public_key = db.create_client(f"test-{uuid.uuid4().hex[:8]}")
    db.set_client_plan(client_id, plan)
    return Tenant(client_id, secret_key, public_key)


@pytest.fixture
def tenant() -> Tenant:
    return make_tenant()


@pytest.fixture
def other_tenant() -> Tenant:
    return make_tenant()


CATALOG = "shop"
Q = {"data_product_type": CATALOG}

SHOES = [
    ("SKU-NIKE-001", "Nike Air Zoom Pegasus", "lightweight road running shoe with responsive cushioning", "running-shoes"),
    ("SKU-ADIDAS-007", "Adidas Adizero Boston", "fast road running shoe with carbon plate cushioning", "running-shoes"),
    ("SKU-ASICS-003", "Asics Gel Kayano", "stable road running shoe with gel cushioning", "running-shoes"),
    ("SKU-SALOMON-010", "Salomon Speedcross", "aggressive trail running shoe with deep lugs", "trail-shoes"),
    ("gid://shopify/Product/123456", "Timberland Winter Boot", "warm waterproof leather winter boot", "boots"),
    ("3f970550-92cd-4e11-a3a2-0a1b2c3d4e5f", "Dr Martens Leather Boot", "sturdy leather boot with thick sole", "boots"),
    ("SKU-CONVERSE-002", "Converse Chuck Taylor", "classic canvas sneaker for everyday casual wear", "sneakers"),
    ("SKU-VANS-004", "Vans Old Skool", "casual skate sneaker with canvas upper", "sneakers"),
]


@pytest.fixture
def seeded(http, tenant) -> Tenant:
    """A tenant with an 8-item catalog using deliberately non-numeric ids."""
    for item_id, title, description, category in SHOES:
        response = http.put(f"/items/{item_id}", params=Q, headers=tenant.secret, json={
            "title": title, "description": description,
            "properties": {"category": category, "price": 100.0, "brand": title.split()[0]},
        })
        assert response.status_code == 201, response.text
    return tenant


@pytest.fixture
def log_records():
    """Structured log lines emitted by the API while the test runs (parsed JSON dicts)."""
    import json

    records: list[dict] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(json.loads(logging.getLogger("likyly.api").handlers[0].formatter.format(record)))

    handler = Capture()
    logger = logging.getLogger("likyly.api")
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)


def interactions_of(tenant: Tenant, **filters) -> list:
    """Raw interaction rows for a tenant (direct DB read - what the API actually stored)."""
    with db.SessionLocal() as session:
        return session.query(db.InteractionModel).filter_by(client_id=tenant.client_id, **filters).order_by(db.InteractionModel.id).all()
