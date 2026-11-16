"""Magento Open Source / Adobe Commerce catalog connector - Magento's REST API (`/rest/<store>/V1/products`),
read with an Integration's credentials.

config: {"store_url": "https://shop.example.com", "store_code": "default", "page_size": 100,
         "url_suffix": ".html", "categories": ["Golf clubs", ...]}  (categories: optional, only those)
credentials: the four values an Integration shows once activated (System -> Extensions ->
Integrations): {"consumer_key", "consumer_secret", "access_token", "access_token_secret"}, or
{"access_token"} alone.

How the requests are authenticated:
- with the four values, every request is signed with OAuth 1.0a (HMAC-SHA256). That works on any
  store, as installed;
- with the access token alone, it is sent as a Bearer token. Since Magento 2.4.4 a store refuses
  that unless "Allow OAuth Access Tokens to be used as standalone Bearer tokens" is on (Stores ->
  Configuration -> Services -> OAuth -> Consumer Settings): the error says so.

What a product becomes: SKU -> id (what the storefront templates, the cart and the orders all
know), name, description, price, the base image as a full URL (the store's media URL +
catalog/product + the file), the product page URL (base URL + url_key + the URL suffix, ".html" by
default - the suffix is not exposed by the REST API, hence `url_suffix`), category names, stock.
The product list carries no stock: the stock items the store manages are read once per sync from
the stock API (what the storefront shows as in / out of stock; a product not listed there is one
whose stock isn't managed, so its stock stays unknown).
Only enabled products visible in the catalog or the search are listed (full sync); an incremental
sync reads every product changed since the last one and drops those disabled or hidden since.

Magento answers a page past the end with the last page again: pages are counted against
`total_count`, never "until an empty page".
"""
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import quote

import requests

from ._retry import network_retry
from .base import Connector, ConnectionTestResult, ConnectorError, NormalizedItem, PreviewResult, SyncPage
from .field_mapping import apply_mapping, detected_fields

_TIMEOUT = 30
_MAX_PAGES = 1000
_VISIBLE = "2,3,4"  # catalog, search, catalog + search (1 = not visible individually)

DEFAULT_MAPPING: dict[str, Any] = {
    "external_id": "sku",
    "title": "name",
    "description": "attributes.description",
    "category": "category_names",
    "price": "price",
    "image": "image_url",
    "url": "product_url",
    "stock": "stock",
    "updated_at": "updated_at",
}


def _enc(value: Any) -> str:
    return quote(str(value), safe="~-._")


def oauth_header(method: str, url: str, params: dict[str, Any], credentials: dict[str, str], *, nonce: Optional[str] = None, timestamp: Optional[str] = None) -> str:
    """OAuth 1.0a `Authorization` header (RFC 5849, HMAC-SHA256), as Magento checks it: the
    signature covers the method, the URL without its query, and every query + oauth parameter."""
    oauth = {
        "oauth_consumer_key": credentials["consumer_key"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": credentials["access_token"],
        "oauth_version": "1.0",
    }
    pairs = sorted((_enc(k), _enc(v)) for k, v in [*params.items(), *oauth.items()])
    base = "&".join([method.upper(), _enc(url), _enc("&".join(f"{k}={v}" for k, v in pairs))])
    key = f"{_enc(credentials['consumer_secret'])}&{_enc(credentials['access_token_secret'])}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in oauth.items())


def _criteria(page: int, page_size: int, filters: list[tuple[str, str, str]]) -> dict[str, Any]:
    """searchCriteria query parameters: one filter group per filter (groups are ANDed)."""
    params: dict[str, Any] = {
        "searchCriteria[pageSize]": page_size,
        "searchCriteria[currentPage]": page,
        "searchCriteria[sortOrders][0][field]": "entity_id",
        "searchCriteria[sortOrders][0][direction]": "ASC",
    }
    for i, (field, condition, value) in enumerate(filters):
        prefix = f"searchCriteria[filterGroups][{i}][filters][0]"
        params[f"{prefix}[field]"] = field
        params[f"{prefix}[value]"] = value
        params[f"{prefix}[conditionType]"] = condition
    return params


class MagentoConnector(Connector):
    supports_incremental = True

    def __init__(self, config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(config, credentials)
        self._store: Optional[dict[str, Any]] = None
        self._categories: Optional[dict[int, str]] = None
        self._stock_cache: Any = None  # None: not read yet; False: not readable

    # --- HTTP -----------------------------------------------------------------------------------

    def _store_url(self) -> str:
        url = str(self.config.get("store_url") or "").strip().rstrip("/")
        if not re.match(r"^https?://", url):
            raise ConnectorError("config.store_url must be the store's address, e.g. https://shop.example.com")
        return url

    def _endpoint(self, path: str) -> str:
        return f"{self._store_url()}/rest/{self.config.get('store_code') or 'default'}/V1{path}"

    def _signed(self) -> bool:
        return all(self.credentials.get(k) for k in ("consumer_key", "consumer_secret", "access_token", "access_token_secret"))

    @network_retry
    def _request(self, url: str, params: dict[str, Any]) -> requests.Response:
        query = "&".join(f"{_enc(k)}={_enc(v)}" for k, v in params.items())
        if self._signed():
            authorization = oauth_header("GET", url, params, self.credentials)
        else:
            authorization = f"Bearer {self.credentials.get('access_token', '')}"
        return requests.get(f"{url}?{query}" if query else url, headers={"Authorization": authorization, "Accept": "application/json"}, timeout=_TIMEOUT)

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        if not self.credentials.get("access_token"):
            raise ConnectorError("Missing credentials: the Integration's access token (and, to sign requests, its consumer key, consumer secret and access token secret)")
        try:
            response = self._request(self._endpoint(path), params or {})
        except requests.RequestException as error:
            raise ConnectorError(f"Could not reach the Magento store at {self._store_url()}: {error}") from error
        message = ""
        try:
            message = str(response.json().get("message", ""))
        except (ValueError, AttributeError):
            pass
        if response.status_code == 401:
            if not self._signed():
                raise ConnectorError(
                    "Magento refused the access token as a Bearer token. Give the Integration's four values "
                    "(consumer key, consumer secret, access token, access token secret), or turn on Stores > "
                    "Configuration > Services > OAuth > Consumer Settings > \"Allow OAuth Access Tokens to be "
                    "used as standalone Bearer tokens\"."
                )
            raise ConnectorError(f"Magento refused the Integration's credentials ({message or 'unauthorized'}): check them, and that the Integration is active")
        if response.status_code == 403 or "isn't authorized" in message:
            raise ConnectorError(f"The Integration may not read this ({message or 'forbidden'}): give it access to Catalog > Products and Categories, and Stores > Settings > All Stores")
        if response.status_code == 404:
            raise ConnectorError(f"No Magento REST API at {self._endpoint(path)}: check the store address and store code")
        if not response.ok:
            raise ConnectorError(f"Magento returned {response.status_code}: {response.text[:300]}")
        try:
            return response.json()
        except ValueError as error:
            raise ConnectorError(f"Magento's answer was not JSON (is {self._store_url()} a Magento store?)") from error

    # --- store context ----------------------------------------------------------------------------

    def _store_config(self) -> dict[str, Any]:
        """The store's public base URL and media URL (for product and image links)."""
        if self._store is None:
            code = self.config.get("store_code") or "default"
            try:
                stores = self._get("/store/storeConfigs") or []
            except ConnectorError:
                stores = []
            found = next((s for s in stores if s.get("code") == code), stores[0] if stores else {})
            base = found.get("secure_base_url") or found.get("base_url") or f"{self._store_url()}/"
            media = found.get("secure_base_media_url") or found.get("base_media_url") or f"{base}media/"
            # A store installed on http keeps its http URLs in "secure" ones too: no harm.
            self._store = {"base_url": base if base.endswith("/") else base + "/", "media_url": media if media.endswith("/") else media + "/"}
        return self._store

    def _category_names(self) -> dict[int, str]:
        if self._categories is None:
            self._categories = {}
            try:
                payload = self._get("/categories/list", {"searchCriteria[pageSize]": 2000, "searchCriteria[currentPage]": 1})
                self._categories = {int(c["id"]): c.get("name", "") for c in payload.get("items", []) if c.get("level", 2) >= 2}
            except ConnectorError:
                pass  # names are a nicety: products still sync without them
        return self._categories

    def _stock_index(self) -> Optional[dict[int, dict[str, Any]]]:
        """{product id: stock item} for every product whose stock the store manages, read once per
        sync from the stock API (`/V1/stockItems/lowStock` below an unreachable quantity: the one
        listing of stock items Magento offers). None when the Integration may not read it: stock
        then stays unknown rather than wrong."""
        if self._stock_cache is None:
            index: dict[int, dict[str, Any]] = {}
            try:
                for page in range(1, _MAX_PAGES + 1):
                    payload = self._get("/stockItems/lowStock", {"scopeId": 0, "qty": 1_000_000_000, "pageSize": 1000, "currentPage": page})
                    items = payload.get("items") or []
                    for item in items:
                        if item.get("product_id") is not None:
                            index[int(item["product_id"])] = item
                    if not items or len(index) >= int(payload.get("total_count") or 0):
                        break
                self._stock_cache = index
            except ConnectorError:
                self._stock_cache = False
        return None if self._stock_cache is False else self._stock_cache

    def _enrich(self, raw: dict[str, Any], stock_item: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """The product as Magento returns it, plus flat fields the default mapping reads:
        `attributes` (custom_attributes as a dict), `image_url`, `product_url`, `category_names`,
        `stock`, `active`."""
        store = self._store_config()
        attributes = {a.get("attribute_code"): a.get("value") for a in raw.get("custom_attributes") or [] if a.get("attribute_code")}
        image = attributes.get("image") or attributes.get("small_image")
        if not image or image == "no_selection":
            gallery = [e for e in raw.get("media_gallery_entries") or [] if not e.get("disabled")]
            image = gallery[0].get("file") if gallery else None
        url_key = attributes.get("url_key")
        suffix = self.config.get("url_suffix", ".html")
        ext = raw.get("extension_attributes") or {}
        names = self._category_names()
        category_ids = [int(link["category_id"]) for link in ext.get("category_links") or [] if str(link.get("category_id", "")).isdigit()]
        if stock_item is None:
            stock_item = ext.get("stock_item")
        if stock_item is None:
            index = self._stock_index()
            # Read, and not listed: a product whose stock the store doesn't manage.
            stock_item = index.get(int(raw.get("id") or 0), {"manage_stock": False}) if index is not None else {}
        return {
            **raw,
            "attributes": attributes,
            "image_url": f"{store['media_url']}catalog/product/{str(image).lstrip('/')}" if image else None,
            "product_url": f"{store['base_url']}{url_key}{suffix}" if url_key else None,
            "category_names": [names[i] for i in category_ids if names.get(i)],
            "stock": self._stock(stock_item),
            "active": raw.get("status") == 1 and raw.get("visibility") in (2, 3, 4) and self._in_chosen_categories(category_ids, names),
        }

    def _in_chosen_categories(self, category_ids: list[int], names: dict[int, str]) -> bool:
        """config.categories (names, as the wizard lists them; null = products without a
        category): only those products are listed - a plan's product limit, a store bigger than
        what the recommendations are for. No config.categories: every product."""
        chosen = self.config.get("categories")
        if not chosen:
            return True
        product_names = [names[i] for i in category_ids if names.get(i)]
        if not product_names:
            return None in chosen
        return any(name in chosen for name in product_names)

    @staticmethod
    def _stock(stock_item: dict[str, Any]) -> Optional[int]:
        """Units in stock; 0 when out of stock; None when the store doesn't count them (stock
        not managed, or sold on backorder)."""
        if not stock_item or stock_item.get("manage_stock") is False:
            return None
        if not stock_item.get("is_in_stock", True):
            return 0
        try:
            units = int(float(stock_item.get("qty") or 0))
        except (TypeError, ValueError):
            return None
        return units if units > 0 else None

    # --- contract ---------------------------------------------------------------------------------

    def _page(self, page: int, filters: list[tuple[str, str, str]], page_size: Optional[int] = None) -> tuple[list[dict[str, Any]], int]:
        size = page_size or int(self.config.get("page_size", 100))
        payload = self._get("/products", _criteria(page, size, filters))
        if not isinstance(payload, dict) or "items" not in payload:
            raise ConnectorError("Unexpected answer from Magento's /V1/products (no items)")
        return [self._enrich(p) for p in payload.get("items") or []], int(payload.get("total_count") or 0)

    _LISTED = [("status", "eq", "1"), ("visibility", "in", _VISIBLE)]

    def test_connection(self) -> ConnectionTestResult:
        try:
            items, total = self._page(1, self._LISTED, page_size=1)
        except ConnectorError as error:
            return ConnectionTestResult(ok=False, message=str(error))
        how = "signed requests (OAuth)" if self._signed() else "Bearer token"
        return ConnectionTestResult(ok=True, message=f"Connected to {self._store_url()} - {total} product(s) listed ({how})", detail={"total_count": total})

    def preview(self, limit: int = 10) -> PreviewResult:
        items, _ = self._page(1, self._LISTED, page_size=limit)
        return PreviewResult(sample=items, detected_fields=detected_fields(items), suggested_mapping=dict(DEFAULT_MAPPING))

    def _pages(self, filters: list[tuple[str, str, str]], checkpoint_tail: dict[str, Any]) -> Iterator[SyncPage]:
        size = int(self.config.get("page_size", 100))
        page = 1
        for _ in range(_MAX_PAGES):
            items, total = self._page(page, filters, size)
            has_more = bool(items) and page * size < total
            yield SyncPage(raw_records=items, checkpoint={"page": page, **checkpoint_tail}, has_more=has_more)
            if not has_more:
                return
            page += 1

    def full_sync(self, checkpoint: Optional[dict[str, Any]] = None) -> Iterator[SyncPage]:
        yield from self._pages(self._LISTED, {})

    def incremental_sync(self, checkpoint: dict[str, Any]) -> Iterator[SyncPage]:
        since = checkpoint.get("last_updated_at")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Every product changed since: those disabled or hidden since are dropped (normalize_item).
        filters = [("updated_at", "gteq", since)] if since else list(self._LISTED)
        yield from self._pages(filters, {"last_updated_at": now})

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        record = raw if "attributes" in raw and "active" in raw else self._enrich(raw)
        item = apply_mapping(record, {**DEFAULT_MAPPING, **field_mapping})
        item.deleted = not record.get("active", True)
        return item
