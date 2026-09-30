"""Flat-file connectors: a CSV the tenant uploaded (re-processed on every sync - "CSV upload"
means "resync from the last file I gave you", not a one-shot import), and a CSV or JSON file
reachable by URL. Both are full-sync only: a flat file has no cursor to page through or filter
by "changed since", so every sync reads it in full - unchanged rows are still cheap thanks to
the sync engine's content-hash skip.
"""
import csv
import io
import json
from typing import Any, Iterator, Optional

import requests

from ._retry import network_retry
from .base import Connector, ConnectionTestResult, ConnectorError, NormalizedItem, PreviewResult, SyncPage
from .field_mapping import apply_mapping, detected_fields, suggest_mapping

_TIMEOUT = 15


class CsvUploadConnector(Connector):
    """config: {"file_path": "<absolute path on disk, written by the upload endpoint>"}."""

    def _read_rows(self) -> list[dict[str, Any]]:
        file_path = self.config.get("file_path")
        if not file_path:
            raise ConnectorError("No file has been uploaded to this data source yet")
        try:
            with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
                return list(csv.DictReader(handle))
        except FileNotFoundError:
            raise ConnectorError(f"Uploaded file is missing on disk: {file_path}")
        except OSError as error:
            raise ConnectorError(f"Could not read the uploaded file: {error}")

    def test_connection(self) -> ConnectionTestResult:
        try:
            rows = self._read_rows()
        except ConnectorError as error:
            return ConnectionTestResult(ok=False, message=str(error))
        return ConnectionTestResult(ok=True, message=f"{len(rows)} row(s) in the uploaded file")

    def preview(self, limit: int = 10) -> PreviewResult:
        rows = self._read_rows()[:limit]
        return PreviewResult(sample=rows, detected_fields=detected_fields(rows), suggested_mapping=suggest_mapping(rows))

    def full_sync(self, checkpoint: Optional[dict[str, Any]] = None) -> Iterator[SyncPage]:
        yield SyncPage(raw_records=self._read_rows(), checkpoint={}, has_more=False)

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        return apply_mapping(raw, field_mapping)


class RemoteFileConnector(Connector):
    """config: {"url": "...", "format": "csv" | "json", "items_path": "..."} (items_path only
    used for "json" - a dotted path to the array, "" if the response body itself is the array).
    credentials: {"header": "Authorization", "value": "Bearer ..."} for a source behind a
    static auth header, or {} for a public file."""

    @network_retry
    def _request(self, headers: dict[str, str]) -> requests.Response:
        return requests.get(self.config["url"], headers=headers, timeout=_TIMEOUT)

    def _fetch_text(self) -> str:
        headers = {}
        header_name = self.credentials.get("header")
        if header_name:
            headers[header_name] = self.credentials.get("value", "")
        try:
            response = self._request(headers)
        except requests.RequestException as error:
            raise ConnectorError(f"Could not fetch {self.config.get('url')} after retries: {error}") from error
        if not response.ok:
            raise ConnectorError(f"Unexpected response {response.status_code} fetching the file")
        return response.text

    def _read_rows(self) -> list[dict[str, Any]]:
        text = self._fetch_text()
        fmt = self.config.get("format", "csv")
        if fmt == "csv":
            return list(csv.DictReader(io.StringIO(text)))
        if fmt == "json":
            try:
                payload = json.loads(text)
            except ValueError as error:
                raise ConnectorError(f"Response was not valid JSON: {error}") from error
            items_path = self.config.get("items_path", "")
            if items_path:
                from .field_mapping import resolve_path
                payload = resolve_path(payload, items_path)
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                raise ConnectorError("items_path did not resolve to a list of records")
            return payload
        raise ConnectorError(f"Unknown format '{fmt}' - expected 'csv' or 'json'")

    def test_connection(self) -> ConnectionTestResult:
        try:
            rows = self._read_rows()
        except ConnectorError as error:
            return ConnectionTestResult(ok=False, message=str(error))
        return ConnectionTestResult(ok=True, message=f"Reachable - {len(rows)} record(s) found")

    def preview(self, limit: int = 10) -> PreviewResult:
        rows = self._read_rows()[:limit]
        return PreviewResult(sample=rows, detected_fields=detected_fields(rows), suggested_mapping=suggest_mapping(rows))

    def full_sync(self, checkpoint: Optional[dict[str, Any]] = None) -> Iterator[SyncPage]:
        yield SyncPage(raw_records=self._read_rows(), checkpoint={}, has_more=False)

    def normalize_item(self, raw: dict[str, Any], field_mapping: dict[str, Any]) -> NormalizedItem:
        return apply_mapping(raw, field_mapping)
