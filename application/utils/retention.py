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

Every run is recorded in `retention_runs` (the back-office's "Conservation des données" page
reads it and can start one). The API runs one process (see Dockerfile): a thread checks every
hour and purges when the last successful run is a day old (start_daily_purge), so restarts
never purge more than once a day. LIKYLY_RETENTION_PURGE=off stops the daily runs (manual runs
still work). By hand: `python retention.py [--dry-run]` with DATABASE_URL set.
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from db import KIND_USER, engine

RETENTION_MONTHS = 13
BATCH_SIZE = 5000  # rows per DELETE: short transactions, no long lock on a busy table
RUN_EVERY = timedelta(days=1)
CHECK_EVERY_SECONDS = 3600
FIRST_CHECK_DELAY_SECONDS = 60  # let the API finish starting before the first check
DAILY, MANUAL = "daily", "manual"

logger = logging.getLogger("likyly.retention")
_run_lock = threading.Lock()  # one purge at a time in this process


class PurgeAlreadyRunning(Exception):
    pass


def cutoff_sql() -> str:
    return f"now() - interval '{int(RETENTION_MONTHS)} months'"


def daily_enabled() -> bool:
    return os.environ.get("LIKYLY_RETENTION_PURGE", "on").lower() != "off"


def _delete_in_batches(statement: str) -> int:
    total = 0
    while True:
        with engine.begin() as conn:
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
    """What a purge would remove right now - for --dry-run and the back-office."""
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
    interactions = _delete_in_batches(f"""
        DELETE FROM interactions WHERE id IN (
            SELECT id FROM interactions WHERE occurred_at < {cutoff_sql()} LIMIT :batch)
    """)
    recommendations = _delete_in_batches(f"""
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
    return {"interactions": interactions, "recommendations": recommendations, "users": users}


# --- Runs, as the back-office sees them ----------------------------------------------------------

_RUN_COLUMNS = "id, trigger, triggered_by, status, started_at, finished_at, interactions, recommendations, users, error"


def _run_row(row) -> dict[str, Any]:
    return dict(row._mapping)


def start_run(trigger: str, triggered_by: Optional[str] = None) -> dict[str, Any]:
    """Records a run as 'running' and holds the lock; finish it with execute_run. Raises
    PurgeAlreadyRunning if one is under way (in this process, or left 'running' less than an
    hour ago by another)."""
    if not _run_lock.acquire(blocking=False):
        raise PurgeAlreadyRunning()
    try:
        with engine.begin() as conn:
            # A run left 'running' by a process that died is closed, not waited for forever.
            conn.execute(text(
                "UPDATE retention_runs SET status = 'failed', finished_at = now(), "
                "error = 'interrupted (the API restarted during the purge)' "
                "WHERE status = 'running' AND started_at < now() - interval '1 hour'"
            ))
            if conn.execute(text("SELECT 1 FROM retention_runs WHERE status = 'running'")).first():
                raise PurgeAlreadyRunning()
            row = conn.execute(text(
                f"INSERT INTO retention_runs (trigger, triggered_by) VALUES (:trigger, :by) RETURNING {_RUN_COLUMNS}"
            ), {"trigger": trigger, "by": triggered_by}).one()
        return _run_row(row)
    except BaseException:
        _run_lock.release()
        raise


def execute_run(run_id: int) -> dict[str, Any]:
    """Runs the purge for a run start_run opened, records the result, releases the lock."""
    try:
        counts: dict[str, Optional[int]]
        try:
            counts = dict(purge_expired_data())
            status, error = "succeeded", None
        except Exception as exc:
            logger.exception("retention purge failed")
            _report(exc)
            counts, status, error = {"interactions": None, "recommendations": None, "users": None}, "failed", type(exc).__name__
        with engine.begin() as conn:
            row = conn.execute(text(
                "UPDATE retention_runs SET status = :status, finished_at = now(), interactions = :interactions, "
                f"recommendations = :recommendations, users = :users, error = :error WHERE id = :id RETURNING {_RUN_COLUMNS}"
            ), {"status": status, "error": error, "id": run_id, **counts}).one()
        logger.info("retention purge %s (%s months): %s", status, RETENTION_MONTHS, counts)
        return _run_row(row)
    finally:
        _run_lock.release()


def run_now(trigger: str = MANUAL, triggered_by: Optional[str] = None) -> dict[str, Any]:
    return execute_run(start_run(trigger, triggered_by)["id"])


def list_runs(limit: int = 30) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {_RUN_COLUMNS} FROM retention_runs ORDER BY started_at DESC, id DESC LIMIT :limit"), {"limit": limit})
        return [_run_row(row) for row in rows]


def last_success_at() -> Optional[datetime]:
    with engine.connect() as conn:
        return conn.execute(text("SELECT max(finished_at) FROM retention_runs WHERE status = 'succeeded'")).scalar_one()


def next_daily_run_at() -> Optional[datetime]:
    """When the daily thread will purge next (to the hour it checks), or None if it is off."""
    if not daily_enabled():
        return None
    last = last_success_at()
    now = datetime.now(timezone.utc)
    return now if last is None or last + RUN_EVERY <= now else last + RUN_EVERY


def status() -> dict[str, Any]:
    """The back-office page: the rule, what is due now, when the next daily run comes, the runs."""
    return {
        "retention_months": RETENTION_MONTHS,
        "daily_enabled": daily_enabled(),
        "next_run_at": next_daily_run_at(),
        "last_success_at": last_success_at(),
        "expired_now": count_expired(),
        "runs": list_runs(),
    }


def _report(exc: BaseException) -> None:
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def _daily_due() -> bool:
    last = last_success_at()
    return last is None or datetime.now(timezone.utc) - last >= RUN_EVERY


def _daily_loop() -> None:
    time.sleep(FIRST_CHECK_DELAY_SECONDS)
    while True:
        try:
            if daily_enabled() and _daily_due():
                run_now(DAILY)
        except PurgeAlreadyRunning:
            pass  # a manual run is under way: it counts as today's
        except Exception as exc:  # never let the thread die: the next check tries again
            logger.exception("retention purge check failed")
            _report(exc)
        time.sleep(CHECK_EVERY_SECONDS)


def start_daily_purge() -> threading.Thread:
    thread = threading.Thread(target=_daily_loop, name="retention-purge", daemon=True)
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
        print(at, run_now(MANUAL, "command line"))
