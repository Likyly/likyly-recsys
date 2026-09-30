"""The Connector contract every data source type implements. Deliberately generic - LIKYLY
catalogs aren't exclusively e-commerce (articles, events, films, listings, ...), so
NormalizedItem's e-commerce-flavored fields (price/image/stock) are all optional and
everything else lives in free-form `attributes`, matching the API's existing ItemUpsert
(application/api/schemas.py), which the sync engine ultimately writes through.

Adding a new source type (Akeneo, Contentful, Strapi, ...) means one new module implementing
this contract plus one new registry.py entry - nothing else in the sync engine or the API
changes, which is the explicit point of having a contract at all.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Optional


class ConnectorError(Exception):
    """Raised by test_connection/preview/full_sync/incremental_sync for anything the caller
    (sync engine or the /test, /preview endpoints) should surface as a clear, user-facing
    error rather than a stack trace - bad credentials, unreachable host, malformed response."""


class NotSupported(ConnectorError):
    """Raised by incremental_sync on a connector/source that only knows how to do a full
    sync (flat files), and by full_sync/incremental_sync on a push-mode connector (WooCommerce,
    generic webhook) - those never fetch, they only ever receive."""


@dataclass
class NormalizedItem:
    """What every connector's normalize_item() produces, and what the sync engine turns into
    an ItemUpsert (application/api/ingestion.upsert_item et al.) - the mapping from this
    generic shape to Likyly's item_id/title/description/properties happens once, in the sync
    engine, not per-connector."""
    external_id: str
    title: str
    description: Optional[str] = None
    categories: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    price: Optional[float] = None
    image: Optional[str] = None
    url: Optional[str] = None
    stock: Optional[int] = None
    updated_at: Optional[datetime] = None
    raw_metadata: Optional[dict[str, Any]] = None
    deleted: bool = False


@dataclass
class SyncPage:
    """One page of raw source records, as full_sync/incremental_sync yield them. `checkpoint`
    is the connector's own idea of "how far into the source have I gotten" after this page -
    the sync engine persists it after every page (not only at the end) so a crash mid-run
    resumes near where it left off instead of from scratch."""
    raw_records: list[dict[str, Any]]
    checkpoint: dict[str, Any]
    has_more: bool


@dataclass
class ConnectionTestResult:
    ok: bool
    message: str
    detail: Optional[dict[str, Any]] = None


@dataclass
class PreviewResult:
    sample: list[dict[str, Any]]
    detected_fields: list[str]
    suggested_mapping: dict[str, Any]


class Connector(ABC):
    """config is the data source's non-secret `config` JSON; credentials is the decrypted
    `credentials_encrypted` blob (empty dict for a connector that needs none, e.g. CSV
    upload). Both come from application/utils/crypto.decrypt_credentials + the DataSourceModel
    row - never persisted, held only for the duration of one call/sync."""

    #: "full" | "incremental" | "push" - what sync_mode a data source of this type may use.
    #: A connector that supports incremental should still support full (used for the first
    #: sync and for re-baselining); a push-mode connector supports neither (see NotSupported).
    supports_incremental: bool = False
    sync_mode_fixed: Optional[str] = None  # set on push-only connectors (e.g. "push")

    def __init__(self, config: dict[str, Any], credentials: dict[str, Any]):
        self.config = config
        self.credentials = credentials

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult: ...

    @abstractmethod
    def preview(self, limit: int = 10) -> PreviewResult: ...

    def full_sync(self, checkpoint: Optional[dict[str, Any]] = None) -> Iterator[SyncPage]:
        raise NotSupported(f"{type(self).__name__} does not support pull-based sync")

    def incremental_sync(self, checkpoint: dict[str, Any]) -> Iterator[SyncPage]:
        raise NotSupported(f"{type(self).__name__} does not support incremental sync")

    @abstractmethod
    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem: ...

    def get_checkpoint(self) -> dict[str, Any]:
        """Default: connectors report their checkpoint via each SyncPage instead; only
        overridden by a connector whose "current position" isn't simply its last page's
        checkpoint (none of the connectors in this package need to)."""
        return {}
