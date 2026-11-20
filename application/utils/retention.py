"""How long LIKYLY keeps what it learns about a shop's visitors, and the purge that enforces it.

GDPR sets no figure: data is kept no longer than its purpose needs (art. 5(1)(e)). For data
read from a visitor's browser, the CNIL's guidance caps the tracker's life at 13 months and the
data collected through it at 25 months. LIKYLY keeps 13 months for both:

  * a recommendation engine needs recent behaviour; 13 months still covers a full year of
    seasons (last year's Christmas when this one comes);
  * the longest performance window of any plan is 365 days, so every report stays whole;
  * the browser's anonymous id lives 13 months too (the SDK renews it, never extends it):
    nothing in the database outlives the identifier it was recorded under.

What goes, once older than RETENTION_MONTHS (every workspace, every catalog):

  * `interactions`: events (views, clicks, add to cart, purchases...) by `occurred_at`;
  * `recommendations`: the trace of each recommendation served, by `created_at`;
  * signed-in customers that nothing recent refers to: their `id_map` row (the shop's own
    customer id) and their `users` profile, once the mapping is that old and no event of
    theirs is left.

Catalog items, placements, models and accounts are not visitor data and are not touched; a
trained model only holds dense vectors, and the next retraining reads what is left.

The API runs one process (see Dockerfile): it runs at start, then every day (see
start_daily_purge). It only deletes when LIKYLY_RETENTION_PURGE=on; otherwise it logs what it
would delete (count_expired) - so the first run against production is a dry run whose counts
are checked before it is switched on. By hand: `python retention.py [--dry-run]` with
DATABASE_URL set.
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import text

from db import KIND_USER, engine

RETENTION_MONTHS = 13
BATCH_SIZE = 5000  # rows per DELETE: short transactions, no long lock on a busy table
PURGE_EVERY_SECONDS = 24 * 3600
FIRST_PURGE_DELAY_SECONDS = 60  # let the API finish starting before the first purge

logger = logging.getLogger("likyly.retention")


def cutoff_sql() -> str:
    return f"now() - interval '{int(RETENTION_MONTHS)} months'"


def _delete_in_batches(conn_factory, statement: str) -> int:
    total = 0
    while True:
        with conn_factory() as conn:
            deleted = conn.execute(text(statement), {"batch": BATCH_SIZE}).rowcount or 0
        total += deleted
        if deleted < BATCH_SIZE:
            return total


_EXPIRED_USERS = f"""
    SELECT m.client_id, m.product_type, m.internal_id
      FROM id_map m
     WHERE m.kind = :kind_user
       AND m.created_at < {cutoff_sql()}
       AND NOT EXISTS (
             SELECT 1 FROM interactions i
              WHERE i.client_id = m.client_id
                AND i.product_type = m.product_type
                AND i.user_id = m.internal_id)
"""


def count_expired() -> dict[str, int]:
    """What a purge would remove right now - for --dry-run and the logs."""
    with engine.connect() as conn:
        interactions = conn.execute(text(f"SELECT count(*) FROM interactions WHERE occurred_at < {cutoff_sql()}")).scalar_one()
        recommendations = conn.execute(text(f"SELECT count(*) FROM recommendations WHERE created_at < {cutoff_sql()}")).scalar_one()
        # Users are counted as they would be after the events above are gone.
        users = conn.execute(text(f"""
            SELECT count(*) FROM id_map m
             WHERE m.kind = :kind_user AND m.created_at < {cutoff_sql()}
               AND NOT EXISTS (
                     SELECT 1 FROM interactions i
                      WHERE i.client_id = m.client_id AND i.product_type = m.product_type
                        AND i.user_id = m.internal_id AND i.occurred_at >= {cutoff_sql()})
        """), {"kind_user": KIND_USER}).scalar_one()
    return {"interactions": interactions, "recommendations": recommendations, "users": users}


def purge_expired_data() -> dict[str, int]:
    """Deletes every visitor record older than RETENTION_MONTHS; returns how many of each."""
    interactions = _delete_in_batches(engine.begin, f"""
        DELETE FROM interactions WHERE id IN (
            SELECT id FROM interactions WHERE occurred_at < {cutoff_sql()} LIMIT :batch)
    """)
    recommendations = _delete_in_batches(engine.begin, f"""
        DELETE FROM recommendations WHERE recommendation_id IN (
            SELECT recommendation_id FROM recommendations WHERE created_at < {cutoff_sql()} LIMIT :batch)
    """)
    with engine.begin() as conn:
        # Profile first, then the mapping it is found through - both in one transaction.
        conn.execute(text(f"""
            DELETE FROM users u USING ({_EXPIRED_USERS}) e
             WHERE u.client_id = e.client_id AND u.product_type = e.product_type AND u.user_id = e.internal_id
        """), {"kind_user": KIND_USER})
        users = conn.execute(text(f"""
            DELETE FROM id_map m USING ({_EXPIRED_USERS}) e
             WHERE m.kind = :kind_user AND m.client_id = e.client_id
               AND m.product_type = e.product_type AND m.internal_id = e.internal_id
        """), {"kind_user": KIND_USER}).rowcount or 0
    result = {"interactions": interactions, "recommendations": recommendations, "users": users}
    logger.info("retention purge (%s months): %s", RETENTION_MONTHS, result)
    return result


def purge_enabled() -> bool:
    return os.environ.get("LIKYLY_RETENTION_PURGE", "").lower() == "on"


def _purge_forever() -> None:
    time.sleep(FIRST_PURGE_DELAY_SECONDS)
    while True:
        try:
            if purge_enabled():
                purge_expired_data()
            else:
                logger.warning("retention purge off (LIKYLY_RETENTION_PURGE != on), would delete: %s", count_expired())
        except Exception:  # never let the purge thread die: tomorrow's run tries again
            logger.exception("retention purge failed")
            try:
                import sentry_sdk
                sentry_sdk.capture_exception()
            except Exception:
                pass
        time.sleep(PURGE_EVERY_SECONDS)


def start_daily_purge() -> threading.Thread:
    thread = threading.Thread(target=_purge_forever, name="retention-purge", daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=f"Delete visitor data older than {RETENTION_MONTHS} months.")
    parser.add_argument("--dry-run", action="store_true", help="only count what would be deleted")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.dry_run:
        print(at, "would delete:", count_expired())
    else:
        print(at, "deleted:", purge_expired_data())
