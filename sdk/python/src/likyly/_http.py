"""The only place that talks HTTP (sync and async twins): auth header, catalog parameter, timeout,
retries with exponential backoff + jitter, error mapping. Every resource goes through ``send``."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

import httpx

from ._ops import RequestOptions, Response, Spec
from .errors import LikylyError, NetworkError, RequestTimeoutError, error_from_response

T = TypeVar("T")

RETRY_BASE_S = 0.5
RETRY_CAP_S = 8.0
#: A Retry-After longer than this is not waited for: the RateLimitError is raised instead.
MAX_RETRY_AFTER_S = 60.0


@dataclass
class HttpConfig:
    api_key: str
    base_url: str
    catalog: Optional[str]
    timeout: float
    max_retries: int
    user_agent: str
    random: Callable[[], float] = random.random


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def retry_delay(config: HttpConfig, error: LikylyError, attempt: int, idempotent: bool) -> Optional[float]:
    """Seconds to wait before the next attempt, or None if this failure must not be retried."""
    backoff = float(config.random() * min(RETRY_CAP_S, RETRY_BASE_S * 2**attempt))  # full jitter
    if error.status_code == 429:
        # Rejected by the rate limiter before reaching the application: nothing was processed, so
        # retrying is safe for every request.
        if error.retry_after is not None:
            return float(error.retry_after) if error.retry_after <= MAX_RETRY_AFTER_S else None
        return backoff
    if not idempotent:
        return None  # the outcome is unknown - never risk a duplicate
    if error.status_code in (502, 503, 504):
        if error.retry_after is not None:
            return float(error.retry_after) if error.retry_after <= MAX_RETRY_AFTER_S else None
        return backoff
    if isinstance(error, (NetworkError, RequestTimeoutError)):
        return backoff
    return None


def _prepare(config: HttpConfig, spec: Spec[Any]) -> Dict[str, Any]:
    query = dict(spec.query)
    if config.catalog:
        query["data_product_type"] = config.catalog
    headers = {"X-API-Key": config.api_key, "Accept": "application/json", "User-Agent": config.user_agent}
    return {"method": spec.method, "url": config.base_url + spec.path, "params": query or None, "headers": headers, "json": spec.body}


def _to_response(resp: httpx.Response) -> Response:
    text = resp.text
    data: Any = None
    if text:
        try:
            data = resp.json()
        except ValueError:
            data = text  # e.g. an HTML 502 page from a proxy
    return Response(status=resp.status_code, headers={k.lower(): v for k, v in resp.headers.items()}, data=data)


def _check(resp: Response) -> Response:
    if resp.status >= 400:
        request_id = resp.headers.get("x-request-id") or (resp.data.get("request_id") if isinstance(resp.data, dict) else None)
        raise error_from_response(resp.status, resp.data, request_id, parse_retry_after(resp.headers.get("retry-after")))
    return resp


class HttpClient:
    def __init__(self, config: HttpConfig, client: httpx.Client, sleep: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self._client = client
        self._sleep = sleep

    def send(self, spec: Spec[T], options: Optional[RequestOptions] = None) -> T:
        max_retries = options.max_retries if options and options.max_retries is not None else self.config.max_retries
        timeout = options.timeout if options and options.timeout is not None else self.config.timeout
        attempt = 0
        while True:
            try:
                return spec.parse(_check(self._once(spec, timeout)))
            except LikylyError as error:
                delay = retry_delay(self.config, error, attempt, spec.idempotent)
                if delay is None or attempt >= max_retries:
                    raise
                attempt += 1
                self._sleep(delay)

    def _once(self, spec: Spec[Any], timeout: float) -> Response:
        try:
            return _to_response(self._client.request(timeout=timeout, **_prepare(self.config, spec)))
        except httpx.TimeoutException as e:
            raise RequestTimeoutError(f"LIKYLY request timed out after {timeout} s") from e
        except httpx.TransportError as e:
            raise NetworkError(f"Could not reach the LIKYLY API: {e}") from e


class AsyncHttpClient:
    def __init__(self, config: HttpConfig, client: httpx.AsyncClient, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.config = config
        self._client = client
        self._sleep = sleep

    async def send(self, spec: Spec[T], options: Optional[RequestOptions] = None) -> T:
        max_retries = options.max_retries if options and options.max_retries is not None else self.config.max_retries
        timeout = options.timeout if options and options.timeout is not None else self.config.timeout
        attempt = 0
        while True:
            try:
                return spec.parse(_check(await self._once(spec, timeout)))
            except LikylyError as error:
                delay = retry_delay(self.config, error, attempt, spec.idempotent)
                if delay is None or attempt >= max_retries:
                    raise
                attempt += 1
                await self._sleep(delay)

    async def _once(self, spec: Spec[Any], timeout: float) -> Response:
        try:
            return _to_response(await self._client.request(timeout=timeout, **_prepare(self.config, spec)))
        except httpx.TimeoutException as e:
            raise RequestTimeoutError(f"LIKYLY request timed out after {timeout} s") from e
        except httpx.TransportError as e:
            raise NetworkError(f"Could not reach the LIKYLY API: {e}") from e
