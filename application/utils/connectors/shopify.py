"""Shopify catalog connector - Admin REST API (the current, documented way to bulk-read
products; GraphQL bulk operations are the alternative but are asynchronous/queue-based and
not needed at the catalog sizes this connects to). Cursor pagination via the `Link` response
header (`page_info`), which is what the REST Admin API has used since the legacy
page/since_id pagination was deprecated - so this always uses the current pagination style,
not the deprecated one.

config: {"shop_domain": "my-shop.myshopify.com", "api_version": "2024-10", "page_size": 100}
credentials: {"access_token": "shpat_..."}

Incremental sync uses `updated_at_min` seeded from the last run's checkpoint - true
push-based incremental (via Shopify's products/update, products/delete webhooks pointing at
POST /data-sources/{id}/push) is a natural upgrade once the infra's public domain is wired to
a webhook secret, but polling with updated_at_min always works standalone, so this connector
does not require it.
"""
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import requests

from ._retry import network_retry
from .base import Connector, ConnectionTestResult, ConnectorError, NormalizedItem, PreviewResult, SyncPage
from .field_mapping import apply_mapping, detected_fields, suggest_mapping

_TIMEOUT = 20
_MAX_PAGES = 500

# Sane defaults for Shopify's product shape - the tenant's own field_mapping (from
# configure_field_mapping) is merged on top of this, so overriding one field doesn't require
# repeating the rest.
DEFAULT_MAPPING: dict[str, Any] = {
    "external_id": "id",
    "title": "title",
    "description": "body_html",
    "category": "product_type",
    "price": "variants[0].price",
    "image": "image.src",
    "url": "handle",
    "stock": "variants[0].inventory_quantity",
    "updated_at": "updated_at",
}


class ShopifyConnector(Connector):
    supports_incremental = True

    def _base_url(self) -> str:
        domain = self.config.get("shop_domain", "")
        version = self.config.get("api_version", "2024-10")
        return f"https://{domain}/admin/api/{version}/products.json"

    def _headers(self) -> dict[str, str]:
        return {"X-Shopify-Access-Token": self.credentials.get("access_token", "")}

    @network_retry
    def _request(self, url: str, params: Optional[dict[str, Any]]) -> requests.Response:
        return requests.get(url, params=params, headers=self._headers(), timeout=_TIMEOUT)

    def _get(self, url: str, params: Optional[dict[str, Any]] = None) -> requests.Response:
        try:
            response = self._request(url, params)
        except requests.RequestException as error:
            raise ConnectorError(f"Request to Shopify failed after retries: {error}") from error
        if response.status_code == 401:
            raise ConnectorError("Shopify rejected the access token")
        if response.status_code == 404:
            raise ConnectorError(f"Shop domain not found: {self.config.get('shop_domain')}")
        if not response.ok:
            raise ConnectorError(f"Shopify returned {response.status_code}: {response.text[:300]}")
        return response

    def test_connection(self) -> ConnectionTestResult:
        try:
            response = self._get(self._base_url(), {"limit": 1})
        except ConnectorError as error:
            return ConnectionTestResult(ok=False, message=str(error))
        count = len(response.json().get("products", []))
        return ConnectionTestResult(ok=True, message=f"Connected to {self.config.get('shop_domain')} - {count} product(s) on the first page")

    def preview(self, limit: int = 10) -> PreviewResult:
        products = self._get(self._base_url(), {"limit": limit}).json().get("products", [])
        return PreviewResult(sample=products, detected_fields=detected_fields(products), suggested_mapping={**suggest_mapping(products), **DEFAULT_MAPPING})

    def _pages(self, params: dict[str, Any]) -> Iterator[SyncPage]:
        page_size = self.config.get("page_size", 100)
        url, query = self._base_url(), {**params, "limit": page_size}
        for _ in range(_MAX_PAGES):
            response = self._get(url, query)
            products = response.json().get("products", [])
            next_url = response.links.get("next", {}).get("url")
            yield SyncPage(raw_records=products, checkpoint={"page_info_url": next_url}, has_more=bool(next_url))
            if not next_url:
                return
            url, query = next_url, {}

    def full_sync(self, checkpoint: Optional[dict[str, Any]] = None) -> Iterator[SyncPage]:
        resume_url = (checkpoint or {}).get("page_info_url")
        if resume_url:
            yield from self._pages_from_url(resume_url)
        else:
            yield from self._pages({})

    def _pages_from_url(self, url: str) -> Iterator[SyncPage]:
        for _ in range(_MAX_PAGES):
            response = self._get(url)
            products = response.json().get("products", [])
            next_url = response.links.get("next", {}).get("url")
            yield SyncPage(raw_records=products, checkpoint={"page_info_url": next_url}, has_more=bool(next_url))
            if not next_url:
                return
            url = next_url

    def incremental_sync(self, checkpoint: dict[str, Any]) -> Iterator[SyncPage]:
        since = checkpoint.get("last_updated_at_min")
        params = {"updated_at_min": since} if since else {}
        new_checkpoint_tail = {"last_updated_at_min": datetime.now(timezone.utc).isoformat()}
        for page in self._pages(params):
            yield SyncPage(raw_records=page.raw_records, checkpoint={**page.checkpoint, **new_checkpoint_tail}, has_more=page.has_more)

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        return apply_mapping(raw, {**DEFAULT_MAPPING, **field_mapping})
