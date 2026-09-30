"""Runs the language-neutral scenarios in sdk/conformance/scenarios.json - the same file every SDK
runs, and that validate_against_openapi.py checks against the OpenAPI document."""
import json
import re
from typing import Any, Dict
from urllib.parse import parse_qsl, urlsplit

import pytest
from conftest import DEFAULTS, SCENARIOS, make_async_client, make_client

import likyly


def snake(name: str) -> str:
    return re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), name)


def convert_args(args: list, method: str) -> tuple:
    """Scenario args are camelCase JSON. Convert the *top-level* keys of option dicts (and of list entries) to
    snake_case kwargs - never anything inside `properties`, whose keys belong to the developer."""
    positional, kwargs = [], {}
    many = method in ("upsertMany", "import", "trackMany")
    for a in args:
        if isinstance(a, dict) and not many:
            kwargs.update({snake(k): v for k, v in a.items()})
        elif many and isinstance(a, list):
            positional.append([{snake(k): v for k, v in e.items()} if isinstance(e, dict) else e for e in a])
        else:
            positional.append(a)
    return positional, kwargs


def method_name(name: str) -> str:
    return "import_" if name == "import" else snake(name)


def pick(obj: Any, path: str) -> Any:
    for key in path.split("."):
        if obj is None:
            return None
        if key == "length":
            return len(obj)
        if isinstance(obj, dict):
            obj = obj.get(key)  # `properties`: the developer's own keys, never renamed
        else:
            obj = obj[int(key)] if key.isdigit() else getattr(obj, snake(key))
    return obj


def assert_request(scenario: Dict[str, Any], calls: list) -> None:
    assert len(calls) == 1, "exactly one HTTP request"
    request = calls[0]
    want = scenario["request"]
    assert request.method == want["method"]
    url = urlsplit(str(request.url))
    assert f"{url.scheme}://{url.netloc}" == DEFAULTS["baseUrl"]
    assert request.url.raw_path.split(b"?")[0].decode() == want["path"], "raw request path"
    assert dict(parse_qsl(url.query)) == want["query"], "query string"
    assert request.headers["x-api-key"] == DEFAULTS["apiKey"]
    assert request.headers["user-agent"].startswith(DEFAULTS["userAgentPrefix"])
    assert request.headers.get("content-type") == ("application/json" if "body" in want else None)
    assert (json.loads(request.content) if request.content else None) == want.get("body"), "request body"


def ids():
    return [s["id"] for s in SCENARIOS["scenarios"]]


def check_error(scenario: Dict[str, Any], exc: BaseException) -> None:
    want = scenario["error"]
    assert type(exc).__name__ == want["class"]
    assert isinstance(exc, likyly.LikylyError)
    assert exc.status_code == want["statusCode"]
    if "requestId" in want:
        assert exc.request_id == want["requestId"]
    if "retryAfter" in want:
        assert exc.retry_after == want["retryAfter"]
    if "message" in want:
        assert exc.message == want["message"]


@pytest.mark.parametrize("scenario", SCENARIOS["scenarios"], ids=ids())
def test_sync(scenario):
    client, script, _ = make_client([scenario["response"]], catalog=scenario.get("config", {}).get("catalog"), max_retries=0)
    resource = getattr(client, scenario["call"]["resource"])
    args, kwargs = convert_args(scenario["call"]["args"], scenario["call"]["method"])
    fn = getattr(resource, method_name(scenario["call"]["method"]))
    if "error" in scenario:
        with pytest.raises(likyly.LikylyError) as info:
            fn(*args, **kwargs)
        check_error(scenario, info.value)
    else:
        result = fn(*args, **kwargs)
        for path, want in scenario.get("expect", {}).items():
            assert pick(result, path) == want, f"result.{path}"
    assert_request(scenario, script.calls)


@pytest.mark.parametrize("scenario", SCENARIOS["scenarios"], ids=ids())
async def test_async(scenario):
    client, script, _ = make_async_client([scenario["response"]], catalog=scenario.get("config", {}).get("catalog"), max_retries=0)
    resource = getattr(client, scenario["call"]["resource"])
    args, kwargs = convert_args(scenario["call"]["args"], scenario["call"]["method"])
    fn = getattr(resource, method_name(scenario["call"]["method"]))
    if "error" in scenario:
        with pytest.raises(likyly.LikylyError) as info:
            await fn(*args, **kwargs)
        check_error(scenario, info.value)
    else:
        result = await fn(*args, **kwargs)
        for path, want in scenario.get("expect", {}).items():
            assert pick(result, path) == want, f"result.{path}"
    assert_request(scenario, script.calls)
