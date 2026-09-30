"""Every source type LIKYLY knows about - what `list_source_types` / `GET /data-sources/types`
returns, and what data_source_service uses to instantiate the right Connector for a data
source's `type`. Adding Akeneo/Contentful/Strapi later is exactly one new connector module
plus one new entry here - nothing in the sync engine, the API routes, or the MCP tools needs
to change, which is the point of having a registry instead of an if/elif chain scattered
across the codebase.
"""
from dataclasses import dataclass
from typing import Type

from .base import Connector
from .csv_file import CsvUploadConnector, RemoteFileConnector
from .rest_api import RestApiConnector
from .shopify import ShopifyConnector
from .webhook import WebhookConnector
from .woocommerce import WooCommerceConnector


@dataclass(frozen=True)
class SourceTypeInfo:
    id: str
    label: str
    description: str
    connector_class: Type[Connector]
    supports_incremental: bool
    requires_credentials: bool
    fixed_sync_mode: str | None = None


SOURCE_TYPES: dict[str, SourceTypeInfo] = {
    "csv_upload": SourceTypeInfo(
        id="csv_upload", label="CSV upload",
        description="A CSV file you upload to Likyly; re-processed on every sync.",
        connector_class=CsvUploadConnector, supports_incremental=False, requires_credentials=False,
    ),
    "csv_url": SourceTypeInfo(
        id="csv_url", label="CSV by URL",
        description="A CSV file hosted at a URL, fetched fresh on every sync.",
        connector_class=RemoteFileConnector, supports_incremental=False, requires_credentials=False,
    ),
    "json_url": SourceTypeInfo(
        id="json_url", label="JSON by URL",
        description="A JSON file/endpoint hosted at a URL, fetched fresh on every sync.",
        connector_class=RemoteFileConnector, supports_incremental=False, requires_credentials=False,
    ),
    "rest_api": SourceTypeInfo(
        id="rest_api", label="Generic REST/JSON API",
        description="Any REST API returning JSON, with configurable pagination and item extraction.",
        connector_class=RestApiConnector, supports_incremental=True, requires_credentials=False,
    ),
    "shopify": SourceTypeInfo(
        id="shopify", label="Shopify",
        description="A Shopify store's product catalog, via the Admin REST API.",
        connector_class=ShopifyConnector, supports_incremental=True, requires_credentials=True,
    ),
    "woocommerce": SourceTypeInfo(
        id="woocommerce", label="WooCommerce",
        description=(
            "Reuses the existing Likyly WordPress plugin's push-based sync (catalog + purchase "
            "events) - this source reports on that activity rather than pulling separately."
        ),
        connector_class=WooCommerceConnector, supports_incremental=False, requires_credentials=False,
        fixed_sync_mode="push",
    ),
    "webhook": SourceTypeInfo(
        id="webhook", label="Webhook / push API",
        description="Any upstream system that pushes catalog changes to Likyly's push endpoint.",
        connector_class=WebhookConnector, supports_incremental=False, requires_credentials=False,
        fixed_sync_mode="push",
    ),
}


def get_source_type(type_: str) -> SourceTypeInfo:
    info = SOURCE_TYPES.get(type_)
    if info is None:
        raise ValueError(f"Unknown data source type '{type_}' - one of {sorted(SOURCE_TYPES)}")
    return info


def build_connector(type_: str, config: dict, credentials: dict) -> Connector:
    return get_source_type(type_).connector_class(config, credentials)
