#!/usr/bin/env bash
# Starts a throwaway LIKYLY API (Postgres+pgvector in Docker, uvicorn from this repo) and writes
# .local-api.env with the URL and a fresh tenant's keys, for the SDKs' live tests.
#
#   sdk/conformance/local-api.sh start   # then: source sdk/conformance/.local-api.env
#   sdk/conformance/local-api.sh stop
#
# Never touches a real database: it is a new container on port 54329. Needs the repo's .venv.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
DB_URL="postgresql://test:test@localhost:54329/likyly_sdk_live"
PID_FILE="$HERE/.local-api.pid"
PORT="${LIKYLY_LOCAL_PORT:-6099}"

case "${1:-}" in
  start)
    docker rm -f likyly-sdk-pg >/dev/null 2>&1 || true
    docker run -d --name likyly-sdk-pg -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=likyly_sdk_live \
      -p 54329:5432 pgvector/pgvector:pg16 >/dev/null
    for _ in $(seq 1 40); do docker exec likyly-sdk-pg pg_isready -U test -d likyly_sdk_live >/dev/null 2>&1 && break; sleep 1; done
    docker exec likyly-sdk-pg psql -U test -d likyly_sdk_live -q -c "CREATE EXTENSION IF NOT EXISTS vector" >/dev/null
    (cd "$ROOT/application/api" && DATABASE_URL="$DB_URL" SUPABASE_URL=https://example.invalid \
      nohup "$PY" -m uvicorn app:app --port "$PORT" >"$HERE/.local-api.log" 2>&1 & echo $! >"$PID_FILE")
    for _ in $(seq 1 90); do curl -s -o /dev/null "http://127.0.0.1:$PORT/openapi.json" && break; sleep 1; done
    KEYS=$(DATABASE_URL="$DB_URL" SUPABASE_URL=https://example.invalid "$PY" -W ignore - <<PYEOF 2>/dev/null | grep '^KEYS'
import sys; sys.path[:0] = ["$ROOT/application/utils"]
import db
c, s, p = db.create_client("sdk-live"); db.set_client_plan(c, "unlimited"); print("KEYS", s, p)
PYEOF
)
    SECRET=$(echo "$KEYS" | cut -d' ' -f2); PUBLIC=$(echo "$KEYS" | cut -d' ' -f3)
    cat >"$HERE/.local-api.env" <<ENVEOF
export LIKYLY_TEST_URL=http://127.0.0.1:$PORT
export LIKYLY_TEST_URL_DOCKER=http://host.docker.internal:$PORT
export LIKYLY_TEST_SECRET_KEY=$SECRET
export LIKYLY_TEST_PUBLIC_KEY=$PUBLIC
ENVEOF
    echo "API up on :$PORT - keys written to sdk/conformance/.local-api.env"
    ;;
  stop)
    [ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE" "$HERE/.local-api.env"
    docker rm -f likyly-sdk-pg >/dev/null 2>&1 || true
    echo "stopped"
    ;;
  *) echo "usage: $0 start|stop"; exit 2 ;;
esac
