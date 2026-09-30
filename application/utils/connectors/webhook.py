"""Generic webhook/push source: nothing pulls from this source, it pushes to Likyly - a
build system, a headless CMS, a PIM, or Shopify's own webhooks (pointed at the same ingress)
call `POST /data-sources/{id}/push` with a JSON body, authenticated by this source's own
push secret (see db.verify_data_source_push_secret) rather than a tenant API key, since the
caller here is the tenant's own upstream system, not the tenant's own client code.

sync_mode is fixed to "push", same as the WooCommerce adapter - full_sync/incremental_sync
don't apply. There is nothing to "preview" from Likyly's side until the first push arrives;
config.sample (a caller-supplied list of example records, used purely for field-mapping UX)
lets configure_field_mapping be driven before that happens.
"""
from typing import Any

from .base import Connector, ConnectionTestResult, ConnectorError, NormalizedItem, PreviewResult
from .field_mapping import apply_mapping, detected_fields, suggest_mapping


class WebhookConnector(Connector):
    sync_mode_fixed = "push"

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(
            ok=True,
            message=(
                "This source has no connection to test - it's ready to receive pushes at "
                "POST /data-sources/{id}/push, authenticated with the push secret shown when "
                "it was created."
            ),
        )

    def preview(self, limit: int = 10) -> PreviewResult:
        sample = (self.config.get("sample") or [])[:limit]
        if not sample:
            raise ConnectorError(
                "No sample to preview yet - either push a real payload first, or set "
                "config.sample to a few example records when creating/updating this source."
            )
        return PreviewResult(sample=sample, detected_fields=detected_fields(sample), suggested_mapping=suggest_mapping(sample))

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        return apply_mapping(raw, field_mapping)
