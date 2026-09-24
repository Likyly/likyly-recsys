"""Public identifiers: what the integrator's own ids look like on the wire, and the ids
LIKYLY mints itself (recommendation_id).

Public ids are opaque strings - "user_123", "SKU-NIKE-001", a UUID, a Shopify
"gid://shopify/Product/123456". Integers are still accepted on input for now (every
pre-existing integration sends them) and normalized to their decimal string; the canonical
representation everywhere on output is the string.
"""
import os
import time
from typing import Annotated, Any

from pydantic import BeforeValidator, WithJsonSchema

MAX_ID_LENGTH = 255

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def normalize_id(value: Any) -> str:
    """int | str -> canonical string id. bool is rejected explicitly (it is an int in
    Python, and `true` is never a meaningful id); floats are rejected because 5.0 vs 5 vs "5"
    would silently become three different ids."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("id must be a string (an integer is accepted for backward compatibility)")
    text_value = str(value)
    if not text_value.strip():
        raise ValueError("id must not be empty")
    if len(text_value) > MAX_ID_LENGTH:
        raise ValueError(f"id must be at most {MAX_ID_LENGTH} characters")
    if any(ord(ch) < 32 for ch in text_value):
        raise ValueError("id must not contain control characters")
    return text_value


# Input schema advertises the transition contract (string, or the legacy integer); the
# validated value is always a str, and the output schema is plain string.
ExternalId = Annotated[
    str,
    BeforeValidator(normalize_id),
    WithJsonSchema(
        {
            "anyOf": [{"type": "string", "maxLength": MAX_ID_LENGTH}, {"type": "integer", "deprecated": True}],
            "description": (
                "Your own identifier, as a string. Integers are still accepted for backward "
                "compatibility and are converted to their decimal string."
            ),
        },
        mode="validation",
    ),
    WithJsonSchema({"type": "string", "maxLength": MAX_ID_LENGTH}, mode="serialization"),
]


def new_ulid() -> str:
    """26-char ULID: 48-bit millisecond timestamp + 80 random bits, Crockford base32. Unlike
    UUIDv4 it sorts by creation time (good for the recommendations table's index locality and
    for eyeballing logs), and it needs no dependency."""
    value = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_recommendation_id() -> str:
    return f"rec_{new_ulid()}"


def numeric_alias(external_id: str) -> int | None:
    """The legacy integer form of an id, when it has one: "42" -> 42, "SKU-1" -> None.
    Used to keep emitting the deprecated `work_id` on responses - only for ids that really
    are canonical integers, so a legacy client sees exactly the value it always sent."""
    if external_id.isascii() and external_id.isdigit() and str(int(external_id)) == external_id:
        value = int(external_id)
        return value if value < 2**31 else None
    return None
