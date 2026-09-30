"""Business logic behind the /data-sources* routes - mirrors the ingestion.py split: routes
in data_sources.py stay thin (auth, params, HTTP status), everything that decides what
creating/testing/syncing a source *means* lives here.
"""
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

import crypto  # noqa: E402
import db  # noqa: E402
from connectors import SOURCE_TYPES, build_connector, get_source_type  # noqa: E402
from connectors.field_mapping import apply_mapping  # noqa: E402
from db import hash_api_key  # noqa: E402
from sync_engine import SyncEngine  # noqa: E402

UPLOADS_DIR = os.path.join(current_dir, "uploads")


class NotFoundError(Exception):
    """A data source that doesn't exist, or doesn't belong to the caller's tenant - the route
    layer turns this into a 404, deliberately indistinguishable from "never existed" (see
    db.py's data source functions, all client_id-scoped)."""


class ValidationError(Exception):
    """A request that's well-formed JSON but semantically wrong (unknown type, missing
    required credentials, ...) - the route layer turns this into a 422."""


class RateLimitError(Exception):
    """A sync was requested too soon after the last one, or too many times today - see
    db.SYNC_COOLDOWN_SECONDS / PLAN_LIMITS['manual_sync_daily_limit']. The route layer turns
    this into a 429."""


class PlanLimitError(Exception):
    """A plan capacity cap (not a rate) was reached - e.g. PLAN_LIMITS['data_source_limit'].
    Same "not now without upgrading" semantics as ingestion.PlanLimitError for the product
    cap; a separate class here (not a shared import) for the same reason scoped_auth.py
    duplicates the secret/public lookup instead of importing app.py. The route layer turns
    this into a 403."""


def list_source_types() -> list[dict[str, Any]]:
    return [
        {
            "id": info.id, "label": info.label, "description": info.description,
            "supports_incremental": info.supports_incremental,
            "requires_credentials": info.requires_credentials,
            "fixed_sync_mode": info.fixed_sync_mode,
        }
        for info in SOURCE_TYPES.values()
    ]


def _require(client_id: int, data_source_id: int) -> dict[str, Any]:
    row = db.get_data_source(client_id, data_source_id)
    if row is None:
        raise NotFoundError(f"No data source '{data_source_id}' for this account")
    return row


def create_data_source(client_id: int, payload) -> dict[str, Any]:
    plan = db.get_client_plan(client_id)
    source_limit = db.get_plan_limits(plan)["data_source_limit"]
    if source_limit is not None and db.count_data_sources(client_id) >= source_limit:
        raise PlanLimitError(db.plan_limit_message(plan, f"{source_limit} data source(s) max"))

    try:
        type_info = get_source_type(payload.type)
    except ValueError as error:
        raise ValidationError(str(error))

    if type_info.fixed_sync_mode:
        sync_mode = type_info.fixed_sync_mode
    else:
        sync_mode = payload.sync_mode or ("incremental" if type_info.supports_incremental else "full")
        if sync_mode not in ("full", "incremental"):
            raise ValidationError("sync_mode must be 'full' or 'incremental'")
        if sync_mode == "incremental" and not type_info.supports_incremental:
            raise ValidationError(f"'{payload.type}' sources don't support incremental sync")

    if type_info.requires_credentials and not payload.credentials:
        raise ValidationError(f"'{payload.type}' sources require `credentials`")

    encrypted = crypto.encrypt_credentials(payload.credentials) if payload.credentials else None
    row = db.create_data_source(
        client_id=client_id, name=payload.name, type_=payload.type, product_type=payload.product_type,
        config=payload.config or {}, sync_mode=sync_mode, credentials_encrypted=encrypted, schedule=payload.schedule,
    )
    if payload.field_mapping:
        row = db.update_data_source(client_id, row["id"], field_mapping=payload.field_mapping) or row

    push_secret = None
    if type_info.fixed_sync_mode == "push":
        push_secret = secrets.token_urlsafe(32)
        db.set_data_source_push_secret_hash(row["id"], hash_api_key(push_secret))
    return row, push_secret


def get_data_source(client_id: int, data_source_id: int) -> dict[str, Any]:
    return _require(client_id, data_source_id)


def list_data_sources(client_id: int) -> list[dict[str, Any]]:
    return db.list_data_sources(client_id)


def update_data_source(client_id: int, data_source_id: int, payload) -> dict[str, Any]:
    existing = _require(client_id, data_source_id)
    fields: dict[str, Any] = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.config is not None:
        fields["config"] = {**(existing.get("config") or {}), **payload.config}
    if payload.credentials is not None:
        fields["credentials_encrypted"] = crypto.encrypt_credentials(payload.credentials)
    if payload.sync_mode is not None:
        type_info = get_source_type(existing["type"])
        if type_info.fixed_sync_mode:
            raise ValidationError(f"'{existing['type']}' sources always sync in '{type_info.fixed_sync_mode}' mode")
        if payload.sync_mode == "incremental" and not type_info.supports_incremental:
            raise ValidationError(f"'{existing['type']}' sources don't support incremental sync")
        fields["sync_mode"] = payload.sync_mode
    if payload.schedule is not None:
        fields["schedule"] = payload.schedule
    if not fields:
        return existing
    return db.update_data_source(client_id, data_source_id, **fields)


def delete_data_source(client_id: int, data_source_id: int) -> None:
    _require(client_id, data_source_id)
    db.delete_data_source(client_id, data_source_id)


def _connector_for(client_id: int, row: dict[str, Any]):
    credentials = crypto.decrypt_credentials(db.get_data_source_credentials_encrypted(client_id, row["id"]))
    return build_connector(row["type"], row.get("config") or {}, credentials)


def test_data_source(client_id: int, data_source_id: int) -> dict[str, Any]:
    row = _require(client_id, data_source_id)
    try:
        result_ok, result_message, result_detail = True, "", None
        result = _connector_for(client_id, row).test_connection()
        result_ok, result_message, result_detail = result.ok, result.message, result.detail
    except Exception as error:  # a misconfigured connector (bad key, missing config field, ...)
        result_ok, result_message, result_detail = False, str(error), None
    db.update_data_source(client_id, data_source_id, status="connected" if result_ok else "error", last_error=None if result_ok else result_message)
    return {"ok": result_ok, "message": result_message, "detail": result_detail}


def preview_data_source(client_id: int, data_source_id: int, limit: int = 10) -> dict[str, Any]:
    row = _require(client_id, data_source_id)
    try:
        result = _connector_for(client_id, row).preview(limit)
    except Exception as error:
        raise ValidationError(str(error))
    return {"sample": result.sample, "detected_fields": result.detected_fields, "suggested_mapping": result.suggested_mapping}


def dry_run_field_mapping(client_id: int, data_source_id: int, mapping: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    row = _require(client_id, data_source_id)
    try:
        sample = _connector_for(client_id, row).preview(limit).sample
    except Exception as error:
        raise ValidationError(str(error))
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(sample):
        try:
            item = apply_mapping(raw, mapping)
            normalized.append({
                "external_id": item.external_id, "title": item.title, "description": item.description,
                "categories": item.categories, "attributes": item.attributes, "price": item.price,
                "image": item.image, "url": item.url, "stock": item.stock,
            })
        except ValueError as error:
            errors.append(f"record {index}: {error}")
    return {"normalized_sample": normalized, "errors": errors}


def save_field_mapping(client_id: int, data_source_id: int, mapping: dict[str, Any]) -> dict[str, Any]:
    _require(client_id, data_source_id)
    if "external_id" not in mapping or "title" not in mapping:
        raise ValidationError("mapping must at least set 'external_id' and 'title'")
    return db.update_data_source(client_id, data_source_id, field_mapping=mapping)


def trigger_sync(client_id: int, data_source_id: int, mode: str) -> dict[str, Any]:
    row = _require(client_id, data_source_id)
    type_info = get_source_type(row["type"])
    if type_info.fixed_sync_mode == "push":
        raise ValidationError(
            f"'{row['type']}' sources sync automatically via push - there is nothing to trigger. "
            "Check get_sync_status / get_catalog_stats for the latest activity."
        )
    if mode not in ("full", "incremental"):
        raise ValidationError("mode must be 'full' or 'incremental'")
    if mode == "incremental" and not type_info.supports_incremental:
        mode = "full"

    recent = db.list_sync_runs(data_source_id, limit=1)
    if recent:
        elapsed = (datetime.now(timezone.utc) - recent[0]["started_at"]).total_seconds()
        if elapsed < db.SYNC_COOLDOWN_SECONDS:
            raise RateLimitError(
                f"A sync was already started {int(elapsed)}s ago for this data source - wait "
                f"{int(db.SYNC_COOLDOWN_SECONDS - elapsed)}s before retrying. Poll "
                "get_sync_status / get_catalog_stats instead of re-triggering."
            )

    plan = db.get_client_plan(client_id)
    sync_limit = db.get_plan_limits(plan)["manual_sync_daily_limit"]
    if sync_limit is not None and db.count_syncs_today(data_source_id) >= sync_limit:
        raise RateLimitError(
            db.plan_limit_message(plan, f"{sync_limit} sync run(s) per day for this data source")
            + " - or try again tomorrow."
        )

    return db.start_sync_run(data_source_id, mode)


def run_sync_job(client_id: int, data_source_id: int, mode: str, run_id: int) -> None:
    """Executed as a FastAPI BackgroundTask by data_sources.py, after trigger_sync has
    already created the sync_run row (so the API response has a pollable id right away)."""
    row = db.get_data_source(client_id, data_source_id)
    if row is None:
        db.finish_sync_run(run_id, "failed", 0, 0, 0, 0, error_summary="Data source was deleted before the sync ran")
        return
    SyncEngine(client_id, row).run(mode, run_id=run_id)


def list_syncs(client_id: int, data_source_id: int, limit: int = 20) -> list[dict[str, Any]]:
    _require(client_id, data_source_id)
    return db.list_sync_runs(data_source_id, limit)


def get_catalog_stats(client_id: int, data_source_id: int) -> dict[str, Any]:
    row = _require(client_id, data_source_id)
    runs = db.list_sync_runs(data_source_id, limit=1)
    return {
        "data_source_id": data_source_id, "product_type": row["product_type"],
        "item_count": db.count_products_in_catalog(client_id, row["product_type"]),
        "last_item_write_at": db.get_last_item_write_at(client_id, row["product_type"]),
        "last_sync": runs[0] if runs else None,
    }


def rotate_push_secret(client_id: int, data_source_id: int) -> str:
    row = _require(client_id, data_source_id)
    if get_source_type(row["type"]).fixed_sync_mode != "push":
        raise ValidationError(f"'{row['type']}' sources don't accept pushes")
    secret = secrets.token_urlsafe(32)
    db.set_data_source_push_secret_hash(data_source_id, hash_api_key(secret))
    return secret


def handle_push(data_source_id: int, raw_secret: str, items: list[dict[str, Any]], deleted_ids: list[str]) -> dict[str, Any]:
    """Authenticated by the source's own push secret, not a tenant API key - this is the
    ingress an upstream system (or a Shopify webhook) calls directly, so there is no tenant
    API key in play at all. Uses the id-only lookup (not tenant-scoped) since the caller has
    no client_id to present - the push secret itself is the only credential."""
    row = db.get_data_source_by_id_any_client(data_source_id)
    if row is None or not db.verify_data_source_push_secret(data_source_id, raw_secret):
        raise NotFoundError("Unknown data source or invalid push secret")

    client_id = row["client_id"]
    field_mapping = row.get("field_mapping") or {}
    run_id = db.start_sync_run(data_source_id, "push")["id"]

    from ingestion import upsert_items_batch, delete_items  # local import: application/api sibling
    from sync_engine import _content_hash, _to_import_entry

    upserted = failed = 0
    errors: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    to_upsert = []
    for raw in items:
        try:
            normalized = apply_mapping(raw, field_mapping)
            to_upsert.append(_to_import_entry(normalized))
            hashes[normalized.external_id] = _content_hash(normalized)
        except ValueError as error:
            failed += 1
            errors.append({"stage": "normalize", "message": str(error)})

    if to_upsert:
        outcome = upsert_items_batch(client_id, row["product_type"], to_upsert)
        upserted += outcome.succeeded
        failed += len(outcome.errors)
        errors.extend({"stage": "upsert", **e} for e in outcome.errors)
    if deleted_ids:
        delete_items(client_id, row["product_type"], deleted_ids)
        db.delete_data_source_item_hashes(data_source_id, deleted_ids)

    if hashes:
        db.upsert_data_source_item_hashes(data_source_id, run_id, hashes)

    status = "partial" if failed else "success"
    db.finish_sync_run(run_id, status, len(items), upserted, len(deleted_ids), failed, error_summary=json.dumps(errors[:20]) if errors else None)
    now = datetime.now(timezone.utc)
    db.update_data_source(client_id, data_source_id, status="connected", last_sync_at=now, last_success_at=now, last_error=None)
    return {"received": len(items) + len(deleted_ids), "upserted": upserted, "deleted": len(deleted_ids), "failed": failed}
