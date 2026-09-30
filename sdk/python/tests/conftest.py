import json
import os
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from likyly import AsyncLikyly, Likyly

SCENARIOS = json.loads((Path(__file__).resolve().parents[2] / "conformance" / "scenarios.json").read_text())
DEFAULTS = SCENARIOS["defaults"]


class Script:
    """Scripted httpx responses replayed in order (the last one repeats); records every request."""

    def __init__(self, steps: List[Dict[str, Any]]) -> None:
        self.steps = steps
        self.calls: List[httpx.Request] = []
        self.index = 0

    def _step(self) -> Dict[str, Any]:
        step = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        return step

    def _respond(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        step = self._step()
        if step.get("fail"):
            raise step["fail"]
        body = step.get("body")
        return httpx.Response(step.get("status", 200), headers=step.get("headers"), json=body) if body is not None else httpx.Response(step.get("status", 200), headers=step.get("headers"))

    def handler(self, request: httpx.Request) -> httpx.Response:
        return self._respond(request)

    async def ahandler(self, request: httpx.Request) -> httpx.Response:
        return self._respond(request)


def make_client(steps: List[Dict[str, Any]], **kw: Any):
    script = Script(steps)
    sleeps: List[float] = []
    client = Likyly(
        kw.pop("api_key", DEFAULTS["apiKey"]), base_url=kw.pop("base_url", DEFAULTS["baseUrl"]),
        http_client=httpx.Client(transport=httpx.MockTransport(script.handler)),
        _sleep=sleeps.append, _random=lambda: 1.0, **kw,
    )
    return client, script, sleeps


def make_async_client(steps: List[Dict[str, Any]], **kw: Any):
    script = Script(steps)
    sleeps: List[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    client = AsyncLikyly(
        kw.pop("api_key", DEFAULTS["apiKey"]), base_url=kw.pop("base_url", DEFAULTS["baseUrl"]),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(script.ahandler)),
        _sleep=fake_sleep, _random=lambda: 1.0, **kw,
    )
    return client, script, sleeps


LIVE = {k: os.environ.get(k) for k in ("LIKYLY_TEST_URL", "LIKYLY_TEST_SECRET_KEY", "LIKYLY_TEST_PUBLIC_KEY")}
live = pytest.mark.skipif(not all(LIVE.values()), reason="LIKYLY_TEST_* not set")
