"""request_id + structured (JSON) logging.

Every request gets a request_id (the caller's X-Request-ID if it sent a sane one, else a
fresh ULID), echoed in the response header and attached to every log line and error body
produced while handling it - so "the recommendation looked wrong" can be traced from the
recommendation_id in the response to the exact log lines of the call that made it.

Never log: API keys (the X-API-Key header is never read here), raw request bodies, user
properties. Log ids and counts.
"""
import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from ids import new_ulid

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def coerce_request_id(incoming: str | None) -> str:
    return incoming if incoming and _SAFE_REQUEST_ID.match(incoming) else f"req_{new_ulid()}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str = "likyly.api") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(getattr(h, "_likyly", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._likyly = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    get_logger().log(level, event, extra={"fields": {"event": event, **fields}})
