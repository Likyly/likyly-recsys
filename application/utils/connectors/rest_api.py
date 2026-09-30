"""Generic REST/JSON API connector - the fallback for "we have a /api/products endpoint"
when there's no dedicated connector (Shopify, ...) for it. Configurable pagination and a
configurable path to the item array in the response, so it fits most JSON APIs without code
changes - a coding agent (or a human) only has to describe the shape once, via `config`.

config shape (all keys optional except base_url):
{
  "base_url": "https://shop.example.com/api/products",
  "items_path": "data.items",        # dotted path to the array; "" = the whole response is the array
  "pagination": {
    "type": "page" | "cursor" | "none",   # default "none" (single page)
    "page_param": "page", "page_size_param": "per_page", "page_size": 100,
    "cursor_param": "cursor", "next_cursor_path": "meta.next_cursor",
  },
  "auth": {"type": "bearer" | "api_key" | "basic" | "none", "header": "X-Api-Key"},
  "updated_since_param": "updated_since",   # query param name used for incremental_sync
}
credentials: {"token": "..."} (bearer/api_key) or {"username": "...", "password": "..."} (basic).
"""
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import requests

from ._retry import network_retry
from .base import Connector, ConnectionTestResult, ConnectorError, NormalizedItem, NotSupported, PreviewResult, SyncPage
from .field_mapping import apply_mapping, detected_fields, suggest_mapping

_TIMEOUT = 15
_MAX_PAGES = 500


class RestApiConnector(Connector):
    supports_incremental = True

    def _auth(self) -> tuple[dict[str, str], Optional[tuple[str, str]]]:
        auth_cfg = self.config.get("auth") or {}
        auth_type = auth_cfg.get("type", "none")
        headers: dict[str, str] = {}
        basic: Optional[tuple[str, str]] = None
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.credentials.get('token', '')}"
        elif auth_type == "api_key":
            headers[auth_cfg.get("header", "X-Api-Key")] = self.credentials.get("token", "")
        elif auth_type == "basic":
            basic = (self.credentials.get("username", ""), self.credentials.get("password", ""))
        return headers, basic

    @network_retry
    def _request(self, params: dict[str, Any]) -> requests.Response:
        headers, basic = self._auth()
        return requests.get(self.config["base_url"], params=params, headers=headers, auth=basic, timeout=_TIMEOUT)

    def _get(self, params: dict[str, Any]) -> Any:
        try:
            response = self._request(params)
        except requests.RequestException as error:
            raise ConnectorError(f"Request failed after retries: {error}") from error
        if response.status_code == 401 or response.status_code == 403:
            raise ConnectorError(f"Authentication rejected ({response.status_code}) - check the source's credentials")
        if not response.ok:
            raise ConnectorError(f"Unexpected response {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as error:
            raise ConnectorError(f"Response was not valid JSON: {error}") from error

    def _extract_items(self, payload: Any) -> list[dict[str, Any]]:
        items_path = self.config.get("items_path", "")
        if items_path:
            from .field_mapping import resolve_path
            payload = resolve_path(payload, items_path)
        if payload is None:
            return []
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            raise ConnectorError("items_path did not resolve to a list of records")
        return payload

    def test_connection(self) -> ConnectionTestResult:
        try:
            payload = self._get({})
            items = self._extract_items(payload)
        except ConnectorError as error:
            return ConnectionTestResult(ok=False, message=str(error))
        return ConnectionTestResult(ok=True, message=f"Reachable - {len(items)} record(s) on the first page")

    def preview(self, limit: int = 10) -> PreviewResult:
        items = self._extract_items(self._get({}))[:limit]
        return PreviewResult(sample=items, detected_fields=detected_fields(items), suggested_mapping=suggest_mapping(items))

    def _pagination_pages(self, extra_params: dict[str, Any]) -> Iterator[SyncPage]:
        pagination = self.config.get("pagination") or {}
        ptype = pagination.get("type", "none")
        page_size = pagination.get("page_size", 100)

        if ptype == "none":
            items = self._extract_items(self._get(extra_params))
            yield SyncPage(raw_records=items, checkpoint={}, has_more=False)
            return

        if ptype == "page":
            page_param = pagination.get("page_param", "page")
            size_param = pagination.get("page_size_param", "per_page")
            page = 1
            for _ in range(_MAX_PAGES):
                items = self._extract_items(self._get({**extra_params, page_param: page, size_param: page_size}))
                has_more = len(items) >= page_size and len(items) > 0
                yield SyncPage(raw_records=items, checkpoint={"page": page}, has_more=has_more)
                if not has_more:
                    return
                page += 1
            return

        if ptype == "cursor":
            cursor_param = pagination.get("cursor_param", "cursor")
            next_cursor_path = pagination.get("next_cursor_path", "next_cursor")
            from .field_mapping import resolve_path
            cursor = extra_params.pop("_cursor", None)
            for _ in range(_MAX_PAGES):
                params = dict(extra_params)
                if cursor:
                    params[cursor_param] = cursor
                payload = self._get(params)
                items = self._extract_items(payload)
                cursor = resolve_path(payload, next_cursor_path)
                has_more = bool(cursor)
                yield SyncPage(raw_records=items, checkpoint={"cursor": cursor}, has_more=has_more)
                if not has_more:
                    return
            return

        raise ConnectorError(f"Unknown pagination.type '{ptype}'")

    def full_sync(self, checkpoint: Optional[dict[str, Any]] = None) -> Iterator[SyncPage]:
        yield from self._pagination_pages({})

    def incremental_sync(self, checkpoint: dict[str, Any]) -> Iterator[SyncPage]:
        param = self.config.get("updated_since_param")
        if not param:
            raise NotSupported("This source has no updated_since_param configured - only full sync is available")
        since = checkpoint.get("last_updated_since")
        extra = {param: since} if since else {}
        new_checkpoint = {"last_updated_since": datetime.now(timezone.utc).isoformat()}
        for page in self._pagination_pages(extra):
            yield SyncPage(raw_records=page.raw_records, checkpoint={**page.checkpoint, **new_checkpoint}, has_more=page.has_more)

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        return apply_mapping(raw, field_mapping)
