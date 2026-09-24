"""The SQL migration against a real LEGACY database (schema captured from the pre-change
db.py): data preserved, ids backfilled, idempotent, schema identical to a fresh install - and
the new API serving that migrated data with the old integer ids intact."""
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import db
import migrate

LEGACY_SCHEMA = (Path(__file__).parent / "fixtures" / "legacy_schema.sql").read_text()
MIGRATION_DB = "likyly_migration_test"
Q = {"data_product_type": "movies"}
SECRET, PUBLIC = "legacy-secret-key", "legacy-public-key"


def _url(database: str) -> str:
    parts = urlparse(os.environ["DATABASE_URL"])
    return urlunparse(parts._replace(path=f"/{database}"))


@pytest.fixture(scope="module")
def legacy_engine():
    admin = psycopg2.connect(_url("postgres"))
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {MIGRATION_DB}")
        cursor.execute(f"CREATE DATABASE {MIGRATION_DB}")
    connection = psycopg2.connect(_url(MIGRATION_DB))
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(LEGACY_SCHEMA)
        cursor.execute("""
            INSERT INTO clients (id, name, secret_key_hash, public_key_hash, plan, is_active, created_at) VALUES
              (1, 'demo', %s, NULL, 'unlimited', true, now()),
              (2, 'legacy customer', %s, %s, 'unlimited', true, now());
            INSERT INTO client_event_types (client_id, event_type, label, weight, created_at) VALUES
              (1, 'purchase', 'Achat', 3.0, now()), (1, 'view', 'Vue', 0.2, now()),
              (2, 'purchase', 'Achat', 3.0, now()), (2, 'view', 'Vue', 0.2, now()),
              (2, 'click', 'Mon clic perso', 1.0, now());
            INSERT INTO products (client_id, product_type, work_id, title, description, genre_1, year, price, updated_at) VALUES
              (2, 'movies', 3,   'Alien',   'space marines hunt a xenomorph aboard a ship', 'SciFi', 1979, 5.0, now()),
              (2, 'movies', 10,  'Aliens',  'space marines hunt xenomorphs on a colony',    'SciFi', 1986, 6.0, now()),
              (2, 'movies', 500, 'Titanic', 'ocean liner romance and an iceberg',           'Drama', 1997, 7.0, now());
            INSERT INTO users (client_id, product_type, user_id, user_firstname, user_lastname, user_age) VALUES
              (2, 'movies', 5, 'Ada', 'Lovelace', 36);
            INSERT INTO interactions (client_id, product_type, work_id, user_id, event_type, quantity, occurred_at) VALUES
              (2, 'movies', 3,   5, 'purchase', 2, now()),
              (2, 'movies', 10,  5, 'view',     1, now()),
              (2, 'movies', 500, 7, 'purchase', 1, now()),
              (2, 'movies', 900, 7, 'view',     1, now());
        """, (db.hash_api_key("demo-secret"), db.hash_api_key(SECRET), db.hash_api_key(PUBLIC)))
    connection.close()
    engine = create_engine(_url(MIGRATION_DB), pool_pre_ping=True)
    yield engine
    engine.dispose()
    with admin.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {MIGRATION_DB}")
    admin.close()


@pytest.fixture(scope="module")
def migrated(legacy_engine):
    with legacy_engine.connect() as conn:
        before = {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in ("products", "users", "interactions", "clients")}
    applied = migrate.run_migrations(legacy_engine)
    return legacy_engine, before, applied


def rows(engine, sql):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


def test_migration_is_applied_once_and_recorded(migrated):
    engine, _, applied = migrated
    assert applied == ["0001_public_api_ids_sessions_recommendations"]
    assert rows(engine, "SELECT version FROM schema_migrations") == [("0001_public_api_ids_sessions_recommendations",)]
    assert migrate.run_migrations(engine) == []  # idempotent


def test_migration_is_safe_to_replay_by_hand(migrated):
    engine, before, _ = migrated
    map_before = rows(engine, "SELECT client_id, product_type, kind, external_id, internal_id FROM id_map ORDER BY 1,2,3,4")
    types_before = rows(engine, "SELECT count(*) FROM client_event_types")
    sql = (migrate.MIGRATIONS_DIR / "0001_public_api_ids_sessions_recommendations.sql").read_text()
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)
    assert rows(engine, "SELECT client_id, product_type, kind, external_id, internal_id FROM id_map ORDER BY 1,2,3,4") == map_before
    assert rows(engine, "SELECT count(*) FROM client_event_types") == types_before
    assert rows(engine, "SELECT count(*) FROM products")[0][0] == before["products"]


def test_no_data_is_lost(migrated):
    engine, before, _ = migrated
    for table, count in before.items():
        assert rows(engine, f"SELECT count(*) FROM {table}")[0][0] == count
    assert rows(engine, "SELECT work_id, user_id, event_type, quantity FROM interactions ORDER BY id") == [
        (3, 5, "purchase", 2), (10, 5, "view", 1), (500, 7, "purchase", 1), (900, 7, "view", 1),
    ]


def test_every_legacy_integer_id_is_backfilled_as_its_decimal_string(migrated):
    engine, _, _ = migrated
    items = rows(engine, "SELECT external_id, internal_id FROM id_map WHERE kind='item' AND client_id=2 ORDER BY internal_id")
    users = rows(engine, "SELECT external_id, internal_id FROM id_map WHERE kind='user' AND client_id=2 ORDER BY internal_id")
    # 900 is only known from an interaction (no products row), 7 only from interactions too
    assert items == [("3", 3), ("10", 10), ("500", 500), ("900", 900)]
    assert users == [("5", 5), ("7", 7)]


def test_new_columns_indexes_and_relaxed_constraint(migrated):
    engine, _, _ = migrated
    columns = {r[0]: r[1] for r in rows(engine, "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name='interactions'")}
    assert columns["user_id"] == "YES"
    assert {"session_id", "event_id", "recommendation_id", "placement", "properties"} <= columns.keys()
    indexes = {r[0] for r in rows(engine, "SELECT indexname FROM pg_indexes WHERE tablename IN ('interactions','id_map','recommendations')")}
    assert {"ix_interactions_client_event_time", "ix_interactions_session", "ix_interactions_recommendation",
            "uq_interactions_event_id", "uq_id_map_internal", "ix_recommendations_client_created"} <= indexes


def test_official_event_types_are_added_without_overwriting_a_tenants_own(migrated):
    engine, _, _ = migrated
    types = {(c, t): (label, w) for c, t, label, w in rows(engine, "SELECT client_id, event_type, label, weight FROM client_event_types")}
    for client in (1, 2):
        for event_type in ("impression", "click", "add_to_cart", "remove_from_cart", "purchase", "view"):
            assert (client, event_type) in types
    assert types[(2, "click")] == ("Mon clic perso", 1.0)          # tenant's own definition untouched
    assert types[(1, "click")] == ("Clic", 0.2)                    # default for everyone else
    assert types[(1, "impression")][1] == 0.0 and types[(1, "add_to_cart")][1] == 1.0


def test_schema_is_identical_to_a_fresh_install(migrated):
    """The SQL migration and create_all must describe the same schema - otherwise a fresh
    database and an upgraded one would behave differently."""
    engine, _, _ = migrated
    query = """SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns
               WHERE table_schema='public' AND table_name <> 'schema_migrations' ORDER BY 1, 2"""
    with db.engine.connect() as fresh_conn:
        fresh = [tuple(r) for r in fresh_conn.execute(text(query))]
    assert rows(engine, query) == fresh

    index_query = "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND indexname NOT LIKE '%pkey' ORDER BY 1"
    with db.engine.connect() as fresh_conn:
        fresh_indexes = [tuple(r) for r in fresh_conn.execute(text(index_query))]
    assert rows(engine, index_query) == fresh_indexes


class TestNewApiOnLegacyData:
    """Point the app at the migrated database and use it the way an existing integration would."""

    @pytest.fixture
    def legacy_http(self, migrated, monkeypatch, api):
        engine, _, _ = migrated
        monkeypatch.setattr(db, "engine", engine)
        monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
        return TestClient(api.app)

    def test_items_keep_their_integer_ids_as_strings(self, legacy_http):
        items = legacy_http.get("/items", params=Q, headers={"X-API-Key": SECRET}).json()
        assert [i["item_id"] for i in items] == ["3", "10", "500"]
        assert items[0]["properties"] == {"category": "SciFi", "year": 1979, "price": 5.0}  # synthesized for legacy rows

    def test_legacy_content_recommendations_still_work(self, legacy_http):
        response = legacy_http.get("/getRec/content/3/2", params=Q, headers={"X-API-Key": PUBLIC})
        assert response.status_code == 200
        assert response.json()[0]["work_id"] == 10 and response.json()[0]["item_id"] == "10"

    def test_legacy_event_bodies_reuse_the_existing_internal_ids(self, legacy_http):
        assert legacy_http.post("/events/view", params=Q, headers={"X-API-Key": PUBLIC}, json={"user_id": 5, "work_id": 500}).status_code == 200
        latest = rows(db.engine, "SELECT work_id, user_id FROM interactions ORDER BY id DESC LIMIT 1")
        assert latest == [(500, 5)]

    def test_user_profiles_keep_legacy_fields_and_expose_properties(self, legacy_http):
        user = legacy_http.get("/users/5", params=Q, headers={"X-API-Key": SECRET}).json()
        assert user["user_firstname"] == "Ada" and user["user_age"] == 36
        assert user["properties"] == {"age": 36, "firstname": "Ada", "lastname": "Lovelace"}

    def test_new_string_ids_never_collide_with_legacy_ids(self, legacy_http):
        legacy_http.put("/items/SKU-NEW", params=Q, headers={"X-API-Key": SECRET}, json={"title": "New", "description": "brand new"})
        legacy_http.post("/events/view", params=Q, headers={"X-API-Key": PUBLIC}, json={"user_id": "someone-new", "item_id": "SKU-NEW"})
        assert rows(db.engine, "SELECT internal_id FROM id_map WHERE client_id=2 AND kind='item' AND external_id='SKU-NEW'") == [(901,)]
        assert rows(db.engine, "SELECT internal_id FROM id_map WHERE client_id=2 AND kind='user' AND external_id='someone-new'") == [(8,)]

    def test_auto_recommendation_on_migrated_data(self, legacy_http):
        response = legacy_http.post("/getRec", params=Q, headers={"X-API-Key": PUBLIC}, json={"item_id": 3, "count": 2})
        assert response.status_code == 200
        assert response.json()["strategy"] == "content" and response.json()["items"][0]["item_id"] == "10"
