"""Runs one sync attempt for a data source, reusing the existing catalog-write primitives in
ingestion.py rather than re-implementing them. This is the one place that turns a connector's
raw records into calls to upsert_items_batch/delete_items - individual connectors never touch
the database directly.

Call sites: application/api/data_source_service.sync_data_source (queued as a background
task, same pattern as app.py's run_generate_model_job) and, indirectly, the MCP
`sync_data_source` tool via that endpoint.
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Self-sufficient regardless of import order: db/crypto/connectors live alongside this file
# (application/utils), ingestion/schemas live in the sibling application/api - both need to be
# on sys.path. app.py already arranges this before importing ingestion.py; this mirrors that
# same bootstrap so sync_engine.py also works if something imports it directly (e.g. a test).
_current_dir = os.path.dirname(os.path.abspath(__file__))
for _dir in (_current_dir, os.path.abspath(os.path.join(_current_dir, "../api"))):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

import crypto  # noqa: E402
import db  # noqa: E402
from connectors import ConnectorError, NotSupported, build_connector, get_source_type  # noqa: E402
from connectors.base import NormalizedItem  # noqa: E402
from ingestion import PlanLimitError, delete_items, upsert_items_batch  # noqa: E402
from schemas import ItemImportEntry  # noqa: E402


def _content_hash(item: NormalizedItem) -> str:
    """Only the fields that would actually change what's stored - `raw_metadata` and
    `updated_at` are deliberately excluded so a source whose only change is its own
    "last modified" bookkeeping doesn't force a re-upsert."""
    payload = {
        "title": item.title, "description": item.description,
        "categories": sorted(item.categories), "attributes": item.attributes,
        "price": item.price, "image": item.image, "url": item.url, "stock": item.stock,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _to_import_entry(item: NormalizedItem) -> ItemImportEntry:
    properties: dict[str, Any] = dict(item.attributes)
    if item.categories:
        properties["category"] = item.categories[0]
        properties["categories"] = item.categories
    if item.price is not None:
        properties["price"] = item.price
    if item.image is not None:
        properties["image"] = item.image
    if item.url is not None:
        properties["url"] = item.url
    if item.stock is not None:
        properties["stock"] = item.stock
    return ItemImportEntry(item_id=item.external_id, title=item.title, description=item.description, properties=properties)


class SyncEngine:
    def __init__(self, client_id: int, data_source: dict[str, Any]):
        self.client_id = client_id
        self.data_source = data_source

    def run(self, mode: str, run_id: int | None = None) -> dict[str, Any]:
        """`run_id`: pass the id of a sync_run row already created via db.start_sync_run (the
        pattern data_source_service.trigger_sync uses, so the API can hand back a pollable id
        immediately instead of waiting for the background task to start) - omit it to let this
        call create its own (handy for direct/test use)."""
        if mode not in ("full", "incremental"):
            raise ValueError("mode must be 'full' or 'incremental'")

        source_id = self.data_source["id"]
        product_type = self.data_source["product_type"]
        type_info = get_source_type(self.data_source["type"])

        if type_info.fixed_sync_mode == "push":
            raise NotSupported(
                f"'{self.data_source['type']}' sources sync automatically via push - there is "
                "nothing to trigger here. Check get_sync_status / get_catalog_stats for the "
                "latest activity that source has already produced."
            )
        if mode == "incremental" and not type_info.supports_incremental:
            raise NotSupported(f"'{self.data_source['type']}' sources don't support incremental sync - use mode='full'")

        credentials = crypto.decrypt_credentials(db.get_data_source_credentials_encrypted(self.client_id, source_id))
        connector = build_connector(self.data_source["type"], self.data_source.get("config") or {}, credentials)
        field_mapping = self.data_source.get("field_mapping") or {}

        run_id = run_id if run_id is not None else db.start_sync_run(source_id, mode)["id"]
        db.update_data_source(self.client_id, source_id, status="syncing")

        items_fetched = items_upserted = items_deleted = items_failed = 0
        errors: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        existing_hashes = db.get_data_source_item_hashes(source_id)
        pending_hashes: dict[str, str] = {}

        try:
            if mode == "incremental":
                # Full sync always restarts from page 1, on purpose: it's the only mode the
                # tombstone diff below trusts as a complete listing, so a resumed/partial full
                # sync could wrongly delete everything before the resume point. Only the
                # incremental cursor (a point-in-time bookmark, not a page position) persists
                # across separate sync calls.
                page_iter = connector.incremental_sync(self.data_source.get("cursor") or {})
            else:
                page_iter = connector.full_sync(None)

            for page in page_iter:
                to_upsert: list[ItemImportEntry] = []
                to_delete: list[str] = []

                for raw in page.raw_records:
                    items_fetched += 1
                    try:
                        item = connector.normalize_item(raw, field_mapping)
                    except Exception as error:
                        items_failed += 1
                        errors.append({"stage": "normalize", "message": str(error)})
                        continue

                    seen_ids.add(item.external_id)
                    if item.deleted:
                        to_delete.append(item.external_id)
                        continue

                    content_hash = _content_hash(item)
                    if existing_hashes.get(item.external_id) == content_hash:
                        pending_hashes[item.external_id] = content_hash
                        continue  # unchanged since the last time this source produced it

                    try:
                        to_upsert.append(_to_import_entry(item))
                        pending_hashes[item.external_id] = content_hash
                    except Exception as error:
                        items_failed += 1
                        errors.append({"stage": "map", "id": item.external_id, "message": str(error)})

                if to_upsert:
                    try:
                        outcome = upsert_items_batch(self.client_id, product_type, to_upsert)
                        items_upserted += outcome.succeeded
                        items_failed += len(outcome.errors)
                        errors.extend({"stage": "upsert", **e} for e in outcome.errors)
                    except PlanLimitError as error:
                        items_failed += len(to_upsert)
                        errors.append({"stage": "upsert", "message": str(error)})
                if to_delete:
                    delete_items(self.client_id, product_type, to_delete)
                    items_deleted += len(to_delete)

                if mode == "incremental":
                    db.update_data_source(self.client_id, source_id, cursor=page.checkpoint)
                if pending_hashes:
                    db.upsert_data_source_item_hashes(source_id, run_id, pending_hashes)
                    pending_hashes = {}

            if mode == "full":
                stale = [external_id for external_id in existing_hashes if external_id not in seen_ids]
                if stale:
                    delete_items(self.client_id, product_type, stale)
                    db.delete_data_source_item_hashes(source_id, stale)
                    items_deleted += len(stale)

            status = "partial" if items_failed else "success"
            db.finish_sync_run(
                run_id, status, items_fetched, items_upserted, items_deleted, items_failed,
                error_summary=json.dumps(errors[:20]) if errors else None,
            )
            now = datetime.now(timezone.utc)
            db.update_data_source(self.client_id, source_id, status="connected", last_sync_at=now, last_success_at=now, last_error=None)
        except Exception as error:
            db.finish_sync_run(run_id, "failed", items_fetched, items_upserted, items_deleted, items_failed, error_summary=str(error))
            db.update_data_source(self.client_id, source_id, status="error", last_sync_at=datetime.now(timezone.utc), last_error=str(error))
            if not isinstance(error, ConnectorError):
                raise
        return db.get_sync_run(source_id, run_id)
