#!/usr/bin/env bash
# Runs every SDK's examples/quickstart against the local API (conformance/local-api.sh start).
# The README code samples are generated from these files, so this proves the samples work.
#
#   conformance/run-examples.sh            # all languages
#   conformance/run-examples.sh ruby       # one of: typescript python php ruby go rust java dotnet
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="$(cd "$HERE/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/.local-api.env"
export LIKYLY_SECRET_KEY="$LIKYLY_TEST_SECRET_KEY" LIKYLY_PUBLIC_KEY="$LIKYLY_TEST_PUBLIC_KEY"
STAMP="$(date +%s)"
ONLY="${1:-all}"
FAILED=()

# report NAME EXIT-CODE OUTPUT
report() {
  echo "=== $1"
  echo "$3" | tail -8
  if [[ "$2" != 0 || "$3" != *"quickstart ok"* ]]; then FAILED+=("$1"); echo "FAILED: $1"; fi
}
want() { [[ "$ONLY" == all || "$ONLY" == "$1" ]]; }

host() { # name, dir, command...   (runs on this machine, against 127.0.0.1)
  local name="$1" dir="$2"; shift 2
  want "$name" || return 0
  local out; out=$(cd "$SDK/$dir" && LIKYLY_BASE_URL="$LIKYLY_TEST_URL" LIKYLY_CATALOG="ex-$name-$STAMP" "$@" 2>&1); report "$name" $? "$out"
}
docker_run() { # name, image, dir, shell-command   (runs in Docker, against host.docker.internal)
  local name="$1" image="$2" dir="$3" cmd="$4"
  want "$name" || return 0
  local out; out=$(docker run --rm -e LIKYLY_BASE_URL="$LIKYLY_TEST_URL_DOCKER" -e LIKYLY_CATALOG="ex-$name-$STAMP" \
    -e LIKYLY_SECRET_KEY -e LIKYLY_PUBLIC_KEY -v "$SDK":/sdk -v "likyly-cache-$name":/cache -w "/sdk/$dir" "$image" sh -c "$cmd" 2>&1)
  report "$name" $? "$out"
}

host typescript js sh -c 'npm run build --silent && node examples/quickstart.mjs'
host python python env PYTHONPATH=src .venv/bin/python examples/quickstart.py
host php php php examples/quickstart.php
host rust rust cargo run --quiet --example quickstart
docker_run ruby ruby:3.3 ruby 'ruby -Ilib examples/quickstart.rb'
docker_run go golang:1.23 go 'GOMODCACHE=/cache go run ./examples/quickstart'
docker_run java maven:3.9-eclipse-temurin-17 java \
  'mvn -B -q -Dmaven.repo.local=/cache compile dependency:build-classpath -Dmdep.outputFile=/tmp/cp.txt >/dev/null && java -cp target/classes:$(cat /tmp/cp.txt) examples/Quickstart.java'
docker_run dotnet mcr.microsoft.com/dotnet/sdk:8.0 dotnet 'NUGET_PACKAGES=/cache dotnet run --project examples/Quickstart -v q --nologo'

if ((${#FAILED[@]})); then echo "examples failed: ${FAILED[*]}"; exit 1; fi
echo "all examples ok"
