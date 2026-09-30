"""Shared auth dependencies for the admin-style routers (data_sources.py, placements.py) that
accept the secret key or a developer key carrying a specific scope - see db.DeveloperKeyModel /
ALLOWED_SCOPES - plus, for a runtime-safe endpoint, the restricted public key too.

Deliberately standalone rather than importing app.py's own get_current_client_id /
get_current_client_id_public_ok: app.py mounts these routers, so the reverse import would be
circular. Small, intentional duplication of the same secret/public lookup, same tradeoff
data_sources.py already made and sdk/mcp/src/client.ts's own docstring calls out for itself.
"""
import os
import sys
from typing import Optional

current_dir = os.path.dirname(os.path.abspath(__file__))
_utils_dir = os.path.abspath(os.path.join(current_dir, "../utils"))
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

from fastapi import BackgroundTasks, HTTPException, Security  # noqa: E402
from fastapi.security import APIKeyHeader  # noqa: E402

from db import get_client_and_scope_by_api_key, get_client_and_scopes_by_developer_key, touch_client_usage  # noqa: E402

api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False,
    description="Your secret key, or a developer key with the scope this endpoint needs - never the restricted public key.",
)


async def resolve_scoped_client_id(scope: str, background_tasks: BackgroundTasks, api_key: Optional[str]) -> int:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    result = get_client_and_scope_by_api_key(api_key)
    if result and result[1] == "secret":
        background_tasks.add_task(touch_client_usage, result[0])
        return result[0]
    developer = get_client_and_scopes_by_developer_key(api_key)
    if developer and scope in developer[1]:
        background_tasks.add_task(touch_client_usage, developer[0])
        return developer[0]
    if result is not None or developer is not None:
        raise HTTPException(status_code=403, detail=f"This endpoint requires the '{scope}' scope - use the secret key or a developer key granted it")
    raise HTTPException(status_code=401, detail="Invalid or inactive X-API-Key")


def require_scope(scope: str):
    async def dependency(background_tasks: BackgroundTasks, api_key: Optional[str] = Security(api_key_header)) -> int:
        return await resolve_scoped_client_id(scope, background_tasks, api_key)
    # Read by app.py's custom_openapi() (_required_key_by_operation) to document, per
    # operation, which scope it actually enforces - the same "state what the code enforces"
    # approach that function already uses for the secret/public key split.
    dependency.likyly_required_scope = scope
    return dependency


async def any_valid_key(background_tasks: BackgroundTasks, api_key: Optional[str] = Security(api_key_header)) -> None:
    """For an endpoint that's static/global registry info, not tenant data (e.g.
    GET /data-sources/types) - any of the three key kinds proves the caller is a real
    integrator without needing a specific scope."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    if get_client_and_scope_by_api_key(api_key) or get_client_and_scopes_by_developer_key(api_key):
        return
    raise HTTPException(status_code=401, detail="Invalid or inactive X-API-Key")


any_valid_key.likyly_required_scope = "any"


async def public_ok_client_id(background_tasks: BackgroundTasks, api_key: Optional[str] = Security(api_key_header)) -> int:
    """Secret-or-public - for a runtime endpoint safe to call from a browser with the public
    key (e.g. POST /placements/{slug}/recommend), mirroring app.py's own
    get_current_client_id_public_ok. Never satisfied by a developer key: that credential is
    for admin/config tooling, not runtime traffic."""
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    result = get_client_and_scope_by_api_key(api_key)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive X-API-Key")
    background_tasks.add_task(touch_client_usage, result[0])
    return result[0]


# Tagged "public_ok" (not a plain scope name) so app.py's custom_openapi() maps it to the
# existing "public" x-required-key vocabulary instead of inventing a new value for it.
public_ok_client_id.likyly_required_scope = "public_ok"
