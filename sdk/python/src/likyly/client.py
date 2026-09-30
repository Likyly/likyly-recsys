from __future__ import annotations

from typing import Any, Callable, Optional

import httpx

from ._http import AsyncHttpClient, HttpClient, HttpConfig
from ._version import DEFAULT_BASE_URL, USER_AGENT
from .aresources import Events as AsyncEvents
from .aresources import Items as AsyncItems
from .aresources import Recommendations as AsyncRecommendations
from .aresources import Users as AsyncUsers
from .errors import ValidationError
from .resources import Events, Items, Recommendations, Users


def _config(
    api_key: str, base_url: Optional[str], catalog: Optional[str], timeout: float, max_retries: int, user_agent: Optional[str],
    rand: Optional[Callable[[], float]],
) -> HttpConfig:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValidationError("api_key is required (create one in your LIKYLY account)")
    config = HttpConfig(
        api_key=api_key, base_url=(base_url or DEFAULT_BASE_URL).rstrip("/"), catalog=catalog, timeout=timeout,
        max_retries=max_retries, user_agent=f"{USER_AGENT} {user_agent}" if user_agent else USER_AGENT,
    )
    if rand is not None:
        config.random = rand
    return config


class Likyly:
    """The LIKYLY client. Four things to know::

        likyly.items            your catalog
        likyly.users            your users (optional)
        likyly.events           what visitors do
        likyly.recommendations  what to show them

    Two API keys: the **secret** key (backend only: items, users, everything) and the **public** key
    (recommendations and event tracking only). Never put the secret key in code that ships to a browser.

    :param api_key: your API key.
    :param base_url: defaults to ``https://api.likyly.com``.
    :param catalog: which of your catalogs to use, if your account has several (omit it if you have one).
    :param timeout: per-request timeout in **seconds** (default 10).
    :param max_retries: automatic retries on transient failures - 429, 502, 503, 504, dropped connections (default 2; 0 disables).
    :param user_agent: appended to the SDK's User-Agent, e.g. ``my-shop/1.4``.
    :param http_client: your own ``httpx.Client`` (proxies, custom transport, tests).
    """

    def __init__(
        self, api_key: str, *, base_url: Optional[str] = None, catalog: Optional[str] = None, timeout: float = 10.0,
        max_retries: int = 2, user_agent: Optional[str] = None, http_client: Optional[httpx.Client] = None,
        _sleep: Optional[Callable[[float], None]] = None, _random: Optional[Callable[[], float]] = None,
    ) -> None:
        config = _config(api_key, base_url, catalog, timeout, max_retries, user_agent, _random)
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client()
        kwargs: Any = {"sleep": _sleep} if _sleep else {}
        http = HttpClient(config, self._client, **kwargs)
        self.items = Items(http)
        self.users = Users(http)
        self.events = Events(http)
        self.recommendations = Recommendations(http)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "Likyly":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class AsyncLikyly:
    """Same as :class:`Likyly`, for ``asyncio`` code: every call is a coroutine. Use ``async with AsyncLikyly(...)``."""

    def __init__(
        self, api_key: str, *, base_url: Optional[str] = None, catalog: Optional[str] = None, timeout: float = 10.0,
        max_retries: int = 2, user_agent: Optional[str] = None, http_client: Optional[httpx.AsyncClient] = None,
        _sleep: Optional[Callable[[float], Any]] = None, _random: Optional[Callable[[], float]] = None,
    ) -> None:
        config = _config(api_key, base_url, catalog, timeout, max_retries, user_agent, _random)
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient()
        kwargs: Any = {"sleep": _sleep} if _sleep else {}
        http = AsyncHttpClient(config, self._client, **kwargs)
        self.items = AsyncItems(http)
        self.users = AsyncUsers(http)
        self.events = AsyncEvents(http)
        self.recommendations = AsyncRecommendations(http)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "AsyncLikyly":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()
