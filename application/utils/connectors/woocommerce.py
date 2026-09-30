"""WooCommerce - reuses the existing integration instead of building a second one.

likyly-wordpress/likyly-connector/likyly-connector.php already pushes catalog items
(PUT /items on save_post, including WooCommerce's 'product' post type once selected in its
settings) and purchase events (POST /events/purchase on woocommerce_order_status_completed)
directly to the recsys API. That plugin is unmodified by this feature: this connector is a
thin, read-only adapter around a source of type "woocommerce" that lets it show up in
list_data_sources/get_sync_status/get_catalog_stats with meaningful status, without
re-implementing a pull-based WooCommerce API client that would fight the plugin for control
of the same catalog.

sync_mode is fixed to "push": full_sync/incremental_sync are intentionally not implemented -
calling POST /data-sources/{id}/sync on a data source of this type is a no-op from the
connector's point of view (see data_source_service.sync_data_source), since there is nothing
for it to pull. "Last sync" instead reflects the most recent write the plugin has already
made to this catalog (see db.get_last_item_write_at), which is a perfectly real signal of
whether the integration is alive.

config: {"site_url": "https://shop.example.com"} (optional - only used for test_connection/
preview; sync activity itself never depends on it). No credentials: the plugin authenticates
its own pushes with the tenant's existing secret key, not through this connector.
"""
from typing import Any

import requests

from .base import Connector, ConnectionTestResult, ConnectorError, NormalizedItem, PreviewResult
from .field_mapping import apply_mapping, detected_fields, suggest_mapping

_TIMEOUT = 10

DEFAULT_MAPPING: dict[str, Any] = {
    "external_id": "id",
    "title": "name",
    "description": "description",
    "category": "categories[0].name",
    "price": "prices.price",
    "image": "images[0].src",
    "stock": "stock_quantity",
    "url": "permalink",
}


class WooCommerceConnector(Connector):
    sync_mode_fixed = "push"

    def test_connection(self) -> ConnectionTestResult:
        site_url = self.config.get("site_url")
        if not site_url:
            return ConnectionTestResult(
                ok=True,
                message=(
                    "No site_url configured - this source syncs automatically whenever the "
                    "Likyly WordPress plugin pushes catalog changes. Nothing to test until "
                    "then; check get_sync_status/get_catalog_stats for real activity."
                ),
            )
        try:
            response = requests.get(f"{site_url.rstrip('/')}/wp-json/", timeout=_TIMEOUT)
        except requests.RequestException as error:
            return ConnectionTestResult(ok=False, message=f"Could not reach {site_url}: {error}")
        if not response.ok:
            return ConnectionTestResult(ok=False, message=f"{site_url} responded {response.status_code}")
        return ConnectionTestResult(ok=True, message=f"{site_url} is reachable. Catalog sync itself is driven by the WordPress plugin's own pushes, not by this check.")

    def preview(self, limit: int = 10) -> PreviewResult:
        site_url = self.config.get("site_url")
        if not site_url:
            raise ConnectorError(
                "Set config.site_url to preview a sample via WooCommerce's public Store API "
                "(/wp-json/wc/store/v1/products) - or skip preview and rely on the mapping "
                "defaults, since the plugin already sends title/description/category/url today."
            )
        try:
            response = requests.get(
                f"{site_url.rstrip('/')}/wp-json/wc/store/v1/products",
                params={"per_page": limit}, timeout=_TIMEOUT,
            )
        except requests.RequestException as error:
            raise ConnectorError(f"Could not reach the WooCommerce Store API: {error}") from error
        if not response.ok:
            raise ConnectorError(f"WooCommerce Store API returned {response.status_code} - it may not be enabled on this store")
        products = response.json()
        if not isinstance(products, list):
            raise ConnectorError("Unexpected response shape from the WooCommerce Store API")
        return PreviewResult(sample=products, detected_fields=detected_fields(products), suggested_mapping={**suggest_mapping(products), **DEFAULT_MAPPING})

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        return apply_mapping(raw, {**DEFAULT_MAPPING, **field_mapping})
