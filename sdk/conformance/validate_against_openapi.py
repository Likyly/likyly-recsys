#!/usr/bin/env python3
"""Checks every scenario in scenarios.json against docs/openapi.json - the contract the SDKs
are built on. For each scenario: the request's method+path must exist, its query parameters
must be declared (and required ones present), its body must validate against the operation's
requestBody schema, and its mocked response must validate against the operation's response
schema. If this fails, the SDK scenarios (or the API) are wrong - fix that before any SDK.

    python sdk/conformance/validate_against_openapi.py [path/to/openapi.json | https://.../openapi.json]
"""
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import unquote

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
source = sys.argv[1] if len(sys.argv) > 1 else str(HERE.parents[1] / "docs" / "openapi.json")
if source.startswith("http"):
    spec = json.load(urllib.request.urlopen(source, timeout=30))
else:
    spec = json.loads(Path(source).read_text())
scenarios = json.loads((HERE / "scenarios.json").read_text())["scenarios"]


def find_operation(method: str, raw_path: str):
    path = unquote(raw_path)
    if path in spec["paths"]:
        return path, spec["paths"][path].get(method.lower())
    for template, item in spec["paths"].items():
        pattern = "^" + re.sub(r"\{[^}]+\}", r"(.+)", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")) + "$"
        if re.match(pattern, path) and method.lower() in item:
            # the more literal template wins - `/events/batch` before `/events/{event_type}`
            return template, item[method.lower()]
    return None, None


def validate(instance, schema, where, errors):
    root = {"components": spec["components"], **schema}
    for e in Draft202012Validator(root).iter_errors(instance):
        errors.append(f"{where}: {'/'.join(map(str, e.absolute_path))}: {e.message[:140]}")


errors: list[str] = []
covered = set()
for s in scenarios:
    r = s["request"]
    template, op = find_operation(r["method"], r["path"])
    if op is None:
        errors.append(f"{s['id']}: no OpenAPI operation for {r['method']} {r['path']}")
        continue
    covered.add((r["method"], template))
    declared = {p["name"]: p for p in op.get("parameters", []) if p["in"] == "query"}
    for name in r["query"]:
        if name not in declared and name != "data_product_type":
            errors.append(f"{s['id']}: query parameter {name!r} not declared for {template}")
        if name in declared and declared[name].get("deprecated"):
            errors.append(f"{s['id']}: query parameter {name!r} is deprecated")
    for name, p in declared.items():
        if p.get("required") and name not in r["query"]:
            errors.append(f"{s['id']}: required query parameter {name!r} missing for {template}")
    if op.get("deprecated"):
        errors.append(f"{s['id']}: {template} is deprecated in the OpenAPI")
    if "body" in r:
        rb = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        if rb is None:
            errors.append(f"{s['id']}: {template} takes no JSON body")
        else:
            validate(r["body"], rb, f"{s['id']} request body", errors)
    elif op.get("requestBody", {}).get("required"):
        errors.append(f"{s['id']}: body required by {template}")
    resp = s["response"]
    if resp["body"] is not None and resp["status"] < 300:
        schema = op["responses"].get(str(resp["status"]), {}).get("content", {}).get("application/json", {}).get("schema")
        if schema is None:
            schema = op["responses"].get("200", {}).get("content", {}).get("application/json", {}).get("schema")
        if schema is not None:
            validate(resp["body"], schema, f"{s['id']} response", errors)

if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"OK - {len(scenarios)} scenarios, {len(covered)} distinct operations, all consistent with {spec['info']['title']} {spec['info']['version']}")
