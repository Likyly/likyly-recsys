"""The language of the few messages the API words for people (the free audit, the upgrade request):
French or English, from the request's Accept-Language - the website sends its page's language.

French when the header is missing or names neither language (nothing changes for existing callers);
otherwise whichever of French and English the caller prefers, and English when it lists only other
languages ("de-DE" - it doesn't prefer French). Messages already in English stay English for everyone.
"""
from typing import Optional

from fastapi import Request

LANGUAGES = ("fr", "en")
DEFAULT_LANGUAGE = "fr"


def language_from_header(header: Optional[str], default: str = DEFAULT_LANGUAGE) -> str:
    """'en-US,en;q=0.9,fr;q=0.8' -> 'en'. The first of fr/en by preference (q, then order)."""
    tags: list[tuple[float, int, str]] = []
    for position, part in enumerate((header or "").split(",")):
        name, _, params = part.strip().partition(";")
        name = name.strip().lower()
        if not name:
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.strip().partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            tags.append((-quality, position, name))
    named = [name for _, _, name in sorted(tags) if name != "*"]
    for name in named:
        primary = name.split("-")[0]
        if primary in LANGUAGES:
            return primary
    return "en" if named else default


def request_language(request: Optional[Request], default: str = DEFAULT_LANGUAGE) -> str:
    """`default` when the request has no Accept-Language: French for what the website shows; the
    developer API's messages, English since always, pass default="en"."""
    header = request.headers.get("accept-language") if request is not None else None
    return language_from_header(header, default)


def pick(request: Optional[Request], fr: str, en: str) -> str:
    """The message in the caller's language: pick(request, fr="Ce rapport n'existe pas.", en="This report doesn't exist.")."""
    return en if request_language(request) == "en" else fr
