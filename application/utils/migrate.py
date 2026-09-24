"""Minimal SQL migration runner - no Alembic dependency.

The schema is created by SQLAlchemy's create_all (see db.init_db), which never alters an
existing table. Anything added to an already-deployed table since - columns, indexes,
relaxed constraints, backfills - lives here as numbered .sql files in ./migrations, applied
in filename order and recorded in `schema_migrations`.

Rules for a migration file (this is what keeps deploys safe - the API applies pending
migrations itself at boot, straight against production):
  * idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING) - also runs as a no-op on a fresh
    database where create_all already produced the final schema
  * additive / non-destructive - never DROP or rewrite existing data
  * one transaction per file (Postgres DDL is transactional): all of it applies, or none

Also runnable by hand: `python migrate.py` (uses DATABASE_URL like the rest of the app).
"""
import os
from pathlib import Path

from sqlalchemy import text

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Arbitrary constant: serializes concurrent boots (several workers / a rolling restart)
# so two processes never apply the same migration at once.
_MIGRATION_LOCK_KEY = 7_270_150_001


def pending_migrations(applied: set[str]) -> list[Path]:
    return [f for f in sorted(MIGRATIONS_DIR.glob("*.sql")) if f.stem not in applied]


def run_migrations(engine) -> list[str]:
    """Applies every pending migration; returns the versions applied by this call."""
    newly_applied: list[str] = []
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        ))
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
        for migration in pending_migrations(applied):
            # exec_driver_sql: the file is raw SQL (casts like ::text, no bind parameters)
            conn.exec_driver_sql(migration.read_text(encoding="utf-8"))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": migration.stem})
            newly_applied.append(migration.stem)
    return newly_applied


if __name__ == "__main__":
    from db import init_db  # noqa: F401  (init_db itself runs create_all then run_migrations)
    init_db()
    print(f"Database at {os.environ.get('DATABASE_URL', '?').split('@')[-1]} is up to date")
