#!/usr/bin/env python3
"""Writes docs/openapi.json - the OpenAPI document exactly as the running API serves it at
/openapi.json (same servers, same schema).

Importing the app runs init_db(), so this needs a database. Point it at a DISPOSABLE one
(never the production DATABASE_URL from application/api/.env):

    TEST_DATABASE_URL=postgresql://test:test@localhost:54329/likyly_test scripts/export_openapi.py

tests/test_openapi.py fails when the committed file is stale, so re-run this after any change
to a route or a schema.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
url = os.environ.get("TEST_DATABASE_URL", "postgresql://test:test@localhost:54329/likyly_test")
if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
    sys.exit(f"Refusing to run against a non-local database ({urlparse(url).hostname}).")

os.environ["DATABASE_URL"] = url
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.pop("SENTRY_DSN", None)
sys.path[:0] = [str(ROOT / "application" / "api"), str(ROOT / "application" / "utils")]

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402

schema = TestClient(app_module.app).get("/openapi.json").json()
target = ROOT / "docs" / "openapi.json"
target.write_text(json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Wrote {target.relative_to(ROOT)}: {len(schema['paths'])} paths, {len(schema['components']['schemas'])} schemas")
