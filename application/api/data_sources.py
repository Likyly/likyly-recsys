"""Routes for tenant-scoped data-source management - see data_source_service.py for the
actual logic. Authenticated either by the existing secret key (full access, unchanged) or by
a new developer key carrying the required scope (see db.DeveloperKeyModel / ALLOWED_SCOPES) -
never by the restricted public key, which stays exactly as limited as it is today.

This router is mounted by app.py; it deliberately does not import anything from app.py (no
circular import) - see scoped_auth.py for the shared, standalone auth dependencies (also used
by placements.py).
"""
import os
import sys
from typing import List, Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, UploadFile

import data_source_service as service  # noqa: E402
from schemas import (  # noqa: E402
    CatalogStats, ConnectionTestResultOut, DataSource, DataSourceCreate, DataSourceCreated,
    DataSourceUpdate, ErrorResponse, FieldMappingDryRunOut, FieldMappingIn, Message,
    PreviewResultOut, PushPayload, PushSecretIssued, SourceTypeOut, SyncRun, SyncTriggerRequest,
)
from scoped_auth import any_valid_key, require_scope  # noqa: E402

router = APIRouter(prefix="/data-sources", tags=["data-sources"])


def _errors(*codes: int) -> dict:
    docs = {
        401: "Missing or invalid X-API-Key.",
        403: "The key presented doesn't carry the scope this endpoint requires.",
        404: "No such data source for this account.",
        422: "The request is malformed, or the referenced source type/config is invalid.",
        429: "Synced too recently, or too many times today - see SYNC_COOLDOWN_SECONDS / manual_sync_daily_limit.",
    }
    return {code: {"model": ErrorResponse, "description": docs[code]} for code in codes}


def _not_found(error: service.NotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def _invalid(error: service.ValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _rate_limited(error: service.RateLimitError) -> HTTPException:
    return HTTPException(status_code=429, detail=str(error))


def _plan_limited(error: service.PlanLimitError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(error))


@router.get("/types", response_model=List[SourceTypeOut], summary="List available source types", dependencies=[Depends(any_valid_key)], responses=_errors(401))
def list_source_types():
    """Every catalog source type LIKYLY can connect to today - what create_data_source's
    `type` accepts. `list_source_types` is the MCP tool a coding agent calls first."""
    return service.list_source_types()


@router.post("", response_model=DataSourceCreated, status_code=201, summary="Create a data source", responses=_errors(401, 403, 422))
def create_data_source(payload: DataSourceCreate, client_id: int = Depends(require_scope("sources:write"))):
    """Registers a new integration - doesn't fetch or write anything yet (see /test,
    /preview, /field-mapping, /sync). credentials (if any) are encrypted at rest and never
    returned by any endpoint afterwards. Free plan: one data source max - upgrade to Pro
    for more."""
    try:
        row, push_secret = service.create_data_source(client_id, payload)
    except service.ValidationError as error:
        raise _invalid(error)
    except service.PlanLimitError as error:
        raise _plan_limited(error)
    return {**row, "push_secret": push_secret}


@router.get("", response_model=List[DataSource], summary="List data sources", responses=_errors(401, 403))
def list_data_sources(client_id: int = Depends(require_scope("sources:read"))):
    """Every data source configured for this account - the `list_data_sources` MCP tool."""
    return service.list_data_sources(client_id)


@router.get("/{data_source_id}", response_model=DataSource, summary="Get a data source", responses=_errors(401, 403, 404, 422))
def get_data_source(data_source_id: int, client_id: int = Depends(require_scope("sources:read"))):
    """One data source by id - never returns credentials, only whether it has any (`has_credentials`)."""
    try:
        return service.get_data_source(client_id, data_source_id)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.patch("/{data_source_id}", response_model=DataSource, summary="Update a data source", responses=_errors(401, 403, 404, 422))
def update_data_source(data_source_id: int, payload: DataSourceUpdate, client_id: int = Depends(require_scope("sources:write"))):
    """Partial update - only the fields given are changed. `config` is merged shallowly
    with the existing config (so updating one key doesn't require resending the rest);
    `credentials`, if given, fully replaces the encrypted blob."""
    try:
        return service.update_data_source(client_id, data_source_id, payload)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)


@router.delete("/{data_source_id}", response_model=Message, summary="Delete a data source", responses=_errors(401, 403, 404, 422))
def delete_data_source(data_source_id: int, client_id: int = Depends(require_scope("sources:write"))):
    """Deletes the configuration and its sync history - never touches catalog items already
    written by past syncs from this source."""
    try:
        service.delete_data_source(client_id, data_source_id)
    except service.NotFoundError as error:
        raise _not_found(error)
    return {"message": f"Data source {data_source_id} deleted"}


@router.post("/{data_source_id}/test", response_model=ConnectionTestResultOut, summary="Test the connection", responses=_errors(401, 403, 404, 422))
def test_data_source(data_source_id: int, client_id: int = Depends(require_scope("sources:write"))):
    """Validates credentials/config reach the source without writing any catalog data -
    the `test_data_source` MCP tool. Always returns 200 with ok=false on failure (never a
    500) so a coding agent can report *why* a connection failed."""
    try:
        return service.test_data_source(client_id, data_source_id)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.post("/{data_source_id}/preview", response_model=PreviewResultOut, summary="Preview a sample + detected fields", responses=_errors(401, 403, 404, 422))
def preview_data_source(data_source_id: int, limit: int = Query(10, ge=1, le=50), client_id: int = Depends(require_scope("sources:read"))):
    """Fetches a small sample from the source (never writes to the catalog) plus a best-effort
    suggested field mapping - what configure_field_mapping should be called with next."""
    try:
        return service.preview_data_source(client_id, data_source_id, limit)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)


@router.post("/{data_source_id}/field-mapping/dry-run", response_model=FieldMappingDryRunOut, summary="Try a field mapping without saving it", responses=_errors(401, 403, 404, 422))
def dry_run_field_mapping(data_source_id: int, payload: FieldMappingIn, limit: int = Query(10, ge=1, le=50), client_id: int = Depends(require_scope("sources:read"))):
    """Applies a candidate mapping to a fresh preview sample and shows what it would
    produce - per-record errors (e.g. a missing external_id) are returned, never raised;
    nothing is written or persisted. Call `configure_field_mapping`'s save step next."""
    try:
        return service.dry_run_field_mapping(client_id, data_source_id, payload.mapping, limit)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)


@router.put("/{data_source_id}/field-mapping", response_model=DataSource, summary="Save a field mapping", responses=_errors(401, 403, 404, 422))
def save_field_mapping(data_source_id: int, payload: FieldMappingIn, client_id: int = Depends(require_scope("sources:write"))):
    """Persists the mapping this source's syncs will use from now on - the `configure_field_mapping` MCP tool's save step."""
    try:
        return service.save_field_mapping(client_id, data_source_id, payload.mapping)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)


@router.post("/{data_source_id}/upload", response_model=DataSource, summary="Upload a CSV file (csv_upload sources only)", responses=_errors(401, 403, 404, 422))
async def upload_data_source_file(data_source_id: int, file: UploadFile = File(...), client_id: int = Depends(require_scope("sources:write"))):
    """Stores the file this source's next sync(s) will read - re-uploading replaces it.
    Only for sources of type `csv_upload`; a URL-based or API-based source's data comes from
    its own `config`, not this endpoint."""
    try:
        row = service.get_data_source(client_id, data_source_id)
    except service.NotFoundError as error:
        raise _not_found(error)
    if row["type"] != "csv_upload":
        raise _invalid(service.ValidationError("Only 'csv_upload' sources accept a file upload"))

    directory = os.path.join(service.UPLOADS_DIR, str(client_id))
    os.makedirs(directory, exist_ok=True)
    file_path = os.path.join(directory, f"{data_source_id}.csv")
    contents = await file.read()
    with open(file_path, "wb") as handle:
        handle.write(contents)
    return service.update_data_source(client_id, data_source_id, DataSourceUpdate(config={"file_path": file_path}))


@router.post("/{data_source_id}/sync", response_model=SyncRun, status_code=202, summary="Trigger a sync", responses=_errors(401, 403, 404, 422, 429))
def sync_data_source(data_source_id: int, payload: SyncTriggerRequest, background_tasks: BackgroundTasks, client_id: int = Depends(require_scope("sources:write"))):
    """Queues a sync as a background job and returns immediately with a `SyncRun` in status
    "running" - poll it (or the latest of get_sync_status) for completion. Falls back to a
    full sync if the source doesn't support incremental. Rate-limited: at least
    `SYNC_COOLDOWN_SECONDS` since this source's last sync attempt, and (on the free plan) a
    daily cap - re-triggering faster than that returns 429, not a queued job."""
    try:
        run = service.trigger_sync(client_id, data_source_id, payload.mode)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)
    except service.RateLimitError as error:
        raise _rate_limited(error)
    background_tasks.add_task(service.run_sync_job, client_id, data_source_id, run["mode"], run["id"])
    return run


@router.get("/{data_source_id}/syncs", response_model=List[SyncRun], summary="Recent sync runs", responses=_errors(401, 403, 404, 422))
def list_syncs(data_source_id: int, limit: int = Query(20, ge=1, le=100), client_id: int = Depends(require_scope("sources:read"))):
    """The `get_sync_status` MCP tool - status/metrics of the most recent sync attempts."""
    try:
        return service.list_syncs(client_id, data_source_id, limit)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.get("/{data_source_id}/stats", response_model=CatalogStats, summary="Catalog stats for this source", responses=_errors(401, 403, 404, 422))
def get_catalog_stats(data_source_id: int, client_id: int = Depends(require_scope("sources:read"))):
    """Item count and last-write time for this source's target catalog, plus its latest sync
    run - the `get_catalog_stats` MCP tool, what a coding agent reports back after a sync
    ("X items synced")."""
    try:
        return service.get_catalog_stats(client_id, data_source_id)
    except service.NotFoundError as error:
        raise _not_found(error)


@router.post("/{data_source_id}/push-secret/rotate", response_model=PushSecretIssued, summary="Rotate a push source's shared secret", responses=_errors(401, 403, 404, 422))
def rotate_push_secret(data_source_id: int, client_id: int = Depends(require_scope("sources:write"))):
    """Only for push-mode sources (woocommerce, webhook) - invalidates the current push
    secret and issues a new one; reconfigure the upstream system's webhook with it."""
    try:
        secret = service.rotate_push_secret(client_id, data_source_id)
    except service.NotFoundError as error:
        raise _not_found(error)
    except service.ValidationError as error:
        raise _invalid(error)
    return {"push_secret": secret}


@router.post("/{data_source_id}/push", summary="Push ingress for webhook/push sources", responses={401: {"model": ErrorResponse, "description": "Missing or invalid X-Push-Secret."}, **_errors(404, 422)})
def push_to_data_source(data_source_id: int, payload: PushPayload, x_push_secret: Optional[str] = Header(None, alias="X-Push-Secret")):
    """Authenticated by the source's own push secret (X-Push-Secret header, from
    create_data_source's response or /push-secret/rotate), never a tenant API key - this is
    the ingress an upstream system (or a Shopify webhook) calls directly."""
    if not x_push_secret:
        raise HTTPException(status_code=401, detail="Missing X-Push-Secret header")
    try:
        return service.handle_push(data_source_id, x_push_secret, payload.items, payload.deleted_ids)
    except service.NotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error))
