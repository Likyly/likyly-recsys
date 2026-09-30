"""Every error the SDK raises extends :class:`LikylyError`.

::

    LikylyError
    ├─ NetworkError              no HTTP response (DNS, connection reset, ...)
    ├─ RequestTimeoutError       the request timed out
    ├─ ValidationError           invalid request - caught by the SDK before sending, or 422 from the API
    └─ ApiError                  the API answered with an error status
       ├─ AuthenticationError    401 - missing / invalid / revoked API key
       ├─ PermissionDeniedError  403 - e.g. a public key used for a secret-key operation, or a plan limit
       ├─ NotFoundError          404
       └─ RateLimitError         429 - see ``retry_after``
"""
from __future__ import annotations

from typing import Any, Optional


class LikylyError(Exception):
    """Base class of every SDK error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        request_id: Optional[str] = None,
        retry_after: Optional[float] = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        #: HTTP status code, when there was an HTTP response.
        self.status_code = status_code
        #: The ``request_id`` of the failed call (also the ``X-Request-ID`` header) - quote it when reporting a problem.
        self.request_id = request_id
        #: Seconds to wait before retrying, from the ``Retry-After`` header (429/503).
        self.retry_after = retry_after
        #: The parsed response body, when there was one.
        self.body = body

    def __str__(self) -> str:
        suffix = f" (request_id={self.request_id})" if self.request_id else ""
        return f"{self.message}{suffix}"


class NetworkError(LikylyError):
    pass


class RequestTimeoutError(LikylyError):
    pass


class ValidationError(LikylyError):
    pass


class ApiError(LikylyError):
    pass


class AuthenticationError(ApiError):
    pass


class PermissionDeniedError(ApiError):
    pass


class NotFoundError(ApiError):
    pass


class RateLimitError(ApiError):
    pass


def error_from_response(status: int, body: Any, request_id: Optional[str], retry_after: Optional[float]) -> LikylyError:
    message = _message(body) or f"LIKYLY API error (HTTP {status})"
    if status == 401:
        return AuthenticationError(message, status_code=status, request_id=request_id, retry_after=retry_after, body=body)
    if status == 403:
        return PermissionDeniedError(message, status_code=status, request_id=request_id, retry_after=retry_after, body=body)
    if status == 404:
        return NotFoundError(message, status_code=status, request_id=request_id, retry_after=retry_after, body=body)
    if status == 422:
        return ValidationError(message, status_code=status, request_id=request_id, retry_after=retry_after, body=body)
    if status == 429:
        return RateLimitError(message, status_code=status, request_id=request_id, retry_after=retry_after, body=body)
    return ApiError(message, status_code=status, request_id=request_id, retry_after=retry_after, body=body)


def _message(body: Any) -> Optional[str]:
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
        return str(detail)
    return None
