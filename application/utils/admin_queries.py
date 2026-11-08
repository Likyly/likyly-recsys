"""Read-only queries for the admin back-office (/admin/* routes in app.py): the platform overview,
one workspace's full picture, the integrations seen, the Pro requests - plus record_integration,
which feeds the integrations from the API's own traffic.

Kept out of db.py, which already holds the tenant-facing data layer: nothing here is ever reached
by a workspace's own key or session - only by an account flagged app_metadata.is_admin.
"""
import re
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import text

from db import SessionLocal, utcnow

# --- Integrations ----------------------------------------------------------------------------

# "likyly-wordpress/1.2.0 WordPress/6.6.2", "likyly-php/1.4.0 likyly-laravel",
# "likyly-wizard/0.8.0 framework/nextjs", "likyly-go/1.0.0 (go1.23.1) my-shop/1.4"
_AGENT_TOKEN = re.compile(r"likyly-([a-z][a-z0-9-]{0,31})(?:/([0-9A-Za-z.+_-]{1,32}))?")
_PLATFORM_TOKENS = [
    (re.compile(r"\bWordPress/([0-9][0-9A-Za-z.+_-]*)"), "WordPress"),
    (re.compile(r"\bDrupal/([0-9][0-9A-Za-z.+_-]*)"), "Drupal"),
    (re.compile(r"\bPrestaShop/([0-9][0-9A-Za-z.+_-]*)"), "PrestaShop"),
    (re.compile(r"\bMagento/([0-9][0-9A-Za-z.+_-]*)"), "Magento"),
    (re.compile(r"\bShopify/([0-9][0-9A-Za-z.+_-]*)"), "Shopify"),
]
_FRAMEWORK_TOKEN = re.compile(r"\bframework/([a-z0-9-]{1,32})")
# Not integrations: the API's own outgoing calls and the website's console.
_IGNORED_AGENTS = {"api", "website", "console"}

# What each agent is, for the back-office ("SDK", "CMS", "Outil").
AGENT_KINDS = {
    "wordpress": "cms", "drupal": "cms", "prestashop": "cms", "magento": "cms", "shopify": "cms",
    "wizard": "tool", "mcp": "tool", "cli": "tool",
}


def agent_kind(agent: str) -> str:
    return AGENT_KINDS.get(agent, "sdk")


def parse_client_agent(header: Optional[str]) -> Optional[dict]:
    """The integration a request comes from, or None when it isn't one of ours (a browser's own
    User-Agent, curl, Postman...). With several likyly-* tokens the last one is the most specific
    wrapper - "likyly-php/1.4 likyly-laravel" is the Laravel package, built on the PHP SDK."""
    if not header:
        return None
    header = header[:300]
    matches = [m for m in _AGENT_TOKEN.finditer(header) if m.group(1) not in _IGNORED_AGENTS]
    if not matches:
        return None
    last = matches[-1]
    agent = last.group(1)
    version = last.group(2)
    if version is None:  # "likyly-laravel" without its own version: borrow the SDK's
        version = next((m.group(2) for m in reversed(matches) if m.group(2)), None)
    platform = None
    for pattern, name in _PLATFORM_TOKENS:
        found = pattern.search(header)
        if found:
            platform = f"{name} {found.group(1)}"
            break
    if platform is None:
        framework = _FRAMEWORK_TOKEN.search(header)
        if framework:
            platform = framework.group(1)
    if platform is None and agent != matches[0].group(1):
        platform = f"likyly-{matches[0].group(1)}"  # Laravel: built on likyly-php
    return {"agent": agent, "version": version, "platform": platform, "user_agent": header}


def record_integration(client_id: int, agent: str, version: Optional[str], platform: Optional[str], user_agent: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text("""
                INSERT INTO client_integrations (client_id, agent, version, platform, user_agent, first_seen_at, last_seen_at)
                VALUES (:client_id, :agent, :version, :platform, :user_agent, now(), now())
                ON CONFLICT (client_id, agent) DO UPDATE SET
                    version = COALESCE(EXCLUDED.version, client_integrations.version),
                    platform = COALESCE(EXCLUDED.platform, client_integrations.platform),
                    user_agent = EXCLUDED.user_agent,
                    last_seen_at = now()
            """),
            {"client_id": client_id, "agent": agent, "version": version, "platform": platform, "user_agent": user_agent},
        )
        session.commit()


def list_integrations() -> list[dict]:
    """Every (workspace, agent) pair seen, most recent first."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT i.client_id, COALESCE(c.display_name, c.name) AS client_name, c.contact_email,
                   i.agent, i.version, i.platform, i.user_agent, i.first_seen_at, i.last_seen_at
            FROM client_integrations i JOIN clients c ON c.id = i.client_id
            ORDER BY i.last_seen_at DESC
        """)).mappings().all()
    return [{**dict(r), "kind": agent_kind(r["agent"])} for r in rows]


# --- Overview --------------------------------------------------------------------------------

def _scalar(session, sql: str, **params: Any) -> int:
    return int(session.execute(text(sql), params).scalar() or 0)


def platform_overview() -> dict:
    """The back-office home: accounts, workspaces, traffic, catalogs, integrations, Pro requests."""
    now = utcnow()
    with SessionLocal() as session:
        def count(sql: str, **params: Any) -> int:
            return _scalar(session, sql, **params)

        d7, d30 = now - timedelta(days=7), now - timedelta(days=30)
        overview: dict[str, Any] = {
            "accounts": count("SELECT COUNT(DISTINCT supabase_user_id) FROM clients WHERE supabase_user_id IS NOT NULL"),
            "workspaces": count("SELECT COUNT(*) FROM clients"),
            "active_workspaces": count("SELECT COUNT(*) FROM clients WHERE is_active"),
            "new_workspaces_7d": count("SELECT COUNT(*) FROM clients WHERE created_at >= :since", since=d7),
            "new_workspaces_30d": count("SELECT COUNT(*) FROM clients WHERE created_at >= :since", since=d30),
            "used_workspaces_7d": count("SELECT COUNT(*) FROM clients WHERE last_used_at >= :since", since=d7),
            "used_workspaces_30d": count("SELECT COUNT(*) FROM clients WHERE last_used_at >= :since", since=d30),
            "workspaces_with_catalog": count("SELECT COUNT(DISTINCT client_id) FROM products"),
            "items": count("SELECT COUNT(*) FROM products"),
            "events_30d": count("SELECT COUNT(*) FROM interactions WHERE occurred_at >= :since", since=d30),
            "recommendations_30d": count("SELECT COUNT(*) FROM recommendations WHERE created_at >= :since", since=d30),
            "requests_today": count("SELECT COALESCE(SUM(request_count), 0) FROM api_usage_daily WHERE date = CURRENT_DATE"),
            "requests_7d": count("SELECT COALESCE(SUM(request_count), 0) FROM api_usage_daily WHERE date > CURRENT_DATE - 7"),
            "requests_30d": count("SELECT COALESCE(SUM(request_count), 0) FROM api_usage_daily WHERE date > CURRENT_DATE - 30"),
            "upgrade_requests_30d": count("SELECT COUNT(*) FROM upgrade_requests WHERE created_at >= :since", since=d30),
        }
        overview["plans"] = [dict(r) for r in session.execute(text(
            "SELECT plan, COUNT(*) AS workspaces FROM clients GROUP BY plan ORDER BY COUNT(*) DESC"
        )).mappings()]
        overview["requests_by_day"] = [dict(r) for r in session.execute(text("""
            SELECT d::date AS date, COALESCE(SUM(u.request_count), 0) AS requests
            FROM generate_series(CURRENT_DATE - 29, CURRENT_DATE, interval '1 day') d
            LEFT JOIN api_usage_daily u ON u.date = d::date
            GROUP BY d ORDER BY d
        """)).mappings()]
        overview["signups_by_week"] = [dict(r) for r in session.execute(text("""
            SELECT date_trunc('week', w)::date AS week, COUNT(c.id) AS workspaces
            FROM generate_series(date_trunc('week', now()) - interval '11 weeks', date_trunc('week', now()), interval '1 week') w
            LEFT JOIN clients c ON date_trunc('week', c.created_at) = w
            GROUP BY w ORDER BY w
        """)).mappings()]
        integrations = session.execute(text(
            "SELECT agent, COUNT(*) AS workspaces, MAX(last_seen_at) AS last_seen_at FROM client_integrations GROUP BY agent ORDER BY COUNT(*) DESC"
        )).mappings().all()
        overview["integrations"] = [{**dict(r), "kind": agent_kind(r["agent"])} for r in integrations]
        overview["data_source_types"] = [dict(r) for r in session.execute(text(
            "SELECT type, COUNT(*) AS sources FROM data_sources GROUP BY type ORDER BY COUNT(*) DESC"
        )).mappings()]
        overview["top_workspaces_30d"] = [dict(r) for r in session.execute(text("""
            SELECT c.id, COALESCE(c.display_name, c.name) AS name, c.contact_email, c.plan,
                   SUM(u.request_count) AS requests
            FROM api_usage_daily u JOIN clients c ON c.id = u.client_id
            WHERE u.date > CURRENT_DATE - 30
            GROUP BY c.id ORDER BY SUM(u.request_count) DESC LIMIT 5
        """)).mappings()]
        overview["latest_workspaces"] = [dict(r) for r in session.execute(text("""
            SELECT id, COALESCE(display_name, name) AS name, contact_email, plan, created_at, last_used_at
            FROM clients ORDER BY created_at DESC LIMIT 5
        """)).mappings()]
        # Workspaces created but never used: the ones worth a nudge.
        overview["stalled_workspaces"] = count(
            "SELECT COUNT(*) FROM clients WHERE last_used_at IS NULL AND created_at < :since", since=now - timedelta(days=2)
        )
    overview["latest_upgrade_requests"] = list_upgrade_requests(limit=5)
    return overview


# --- One workspace -----------------------------------------------------------------------------

def client_insights(client_id: int) -> Optional[dict]:
    """Everything the back-office shows about one workspace beyond its admin row: catalogs and
    their volumes, connectors, placements, integrations, events and recommendations served,
    developer keys, Pro requests, and the owner's other workspaces."""
    since = utcnow() - timedelta(days=30)
    with SessionLocal() as session:
        owner = session.execute(
            text("SELECT supabase_user_id, contact_email FROM clients WHERE id = :id"), {"id": client_id}
        ).mappings().first()
        if owner is None:
            return None
        p = {"id": client_id, "since": since}

        catalogs = [dict(r) for r in session.execute(text("""
            SELECT pr.product_type AS catalog, COUNT(*) AS items,
                   COUNT(pr.embedding) AS items_with_embedding, MAX(pr.updated_at) AS last_item_update,
                   (SELECT COUNT(*) FROM users u WHERE u.client_id = :id AND u.product_type = pr.product_type) AS users,
                   (SELECT COUNT(*) FROM interactions i WHERE i.client_id = :id AND i.product_type = pr.product_type) AS events,
                   (SELECT COUNT(*) FROM interactions i WHERE i.client_id = :id AND i.product_type = pr.product_type
                       AND i.occurred_at >= :since) AS events_30d,
                   (SELECT MAX(trained_at) FROM model_versions m WHERE m.client_id = :id AND m.product_type = pr.product_type
                       AND m.is_active) AS model_trained_at
            FROM products pr WHERE pr.client_id = :id
            GROUP BY pr.product_type ORDER BY COUNT(*) DESC
        """), p).mappings()]

        events_by_type = [dict(r) for r in session.execute(text("""
            SELECT event_type, COUNT(*) AS events FROM interactions
            WHERE client_id = :id AND occurred_at >= :since GROUP BY event_type ORDER BY COUNT(*) DESC
        """), p).mappings()]

        recommendations = session.execute(text("""
            SELECT COUNT(*) AS served_30d, MAX(created_at) AS last_served_at,
                   COUNT(DISTINCT placement) AS placements_used
            FROM recommendations WHERE client_id = :id AND created_at >= :since
        """), p).mappings().first()

        data_sources = [dict(r) for r in session.execute(text("""
            SELECT id, name, type, product_type AS catalog, sync_mode, schedule, status,
                   last_sync_at, last_success_at, last_error, created_at
            FROM data_sources WHERE client_id = :id ORDER BY created_at
        """), p).mappings()]

        placements = [dict(r) for r in session.execute(text("""
            SELECT slug, name, context_type, product_type AS catalog, strategy, enabled, updated_at
            FROM placements WHERE client_id = :id ORDER BY created_at
        """), p).mappings()]

        integrations = [
            {**dict(r), "kind": agent_kind(r["agent"])}
            for r in session.execute(text("""
                SELECT agent, version, platform, user_agent, first_seen_at, last_seen_at
                FROM client_integrations WHERE client_id = :id ORDER BY last_seen_at DESC
            """), p).mappings()
        ]

        developer_keys = _scalar(session, "SELECT COUNT(*) FROM developer_keys WHERE client_id = :id AND revoked_at IS NULL", id=client_id)

        sibling_workspaces = []
        if owner["supabase_user_id"]:
            sibling_workspaces = [dict(r) for r in session.execute(text("""
                SELECT id, COALESCE(display_name, name) AS name, plan, created_at
                FROM clients WHERE supabase_user_id = :owner AND id <> :id ORDER BY created_at
            """), {"owner": owner["supabase_user_id"], "id": client_id}).mappings()]

    return {
        "catalogs": catalogs,
        "events_by_type_30d": events_by_type,
        "recommendations_served_30d": int(recommendations["served_30d"] or 0),
        "last_recommendation_at": recommendations["last_served_at"],
        "data_sources": data_sources,
        "placements": placements,
        "integrations": integrations,
        "developer_keys": developer_keys,
        "upgrade_requests": list_upgrade_requests(client_id=client_id),
        "owner_supabase_user_id": owner["supabase_user_id"],
        "other_workspaces": sibling_workspaces,
    }


# --- Pro requests ----------------------------------------------------------------------------

def list_upgrade_requests(limit: int = 100, client_id: Optional[int] = None) -> list[dict]:
    where = "WHERE r.client_id = :client_id" if client_id is not None else ""
    with SessionLocal() as session:
        rows = session.execute(text(f"""
            SELECT r.id, r.client_id, COALESCE(c.display_name, c.name) AS client_name, c.contact_email,
                   c.plan, r.payload, r.created_at, r.emailed_at, r.email_error
            FROM upgrade_requests r JOIN clients c ON c.id = r.client_id
            {where}
            ORDER BY r.created_at DESC LIMIT :limit
        """), {"limit": limit, "client_id": client_id}).mappings().all()
    return [dict(r) for r in rows]


def workspaces_by_owner() -> dict[str, list[dict]]:
    """supabase_user_id -> its workspaces, for the back-office's users page."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT c.supabase_user_id, c.id, COALESCE(c.display_name, c.name) AS name, c.plan, c.is_active,
                   c.last_used_at, (SELECT COUNT(*) FROM products WHERE client_id = c.id) AS item_count
            FROM clients c WHERE c.supabase_user_id IS NOT NULL ORDER BY c.created_at
        """)).mappings().all()
    owners: dict[str, list[dict]] = {}
    for r in rows:
        row = dict(r)
        owners.setdefault(row.pop("supabase_user_id"), []).append(row)
    return owners
