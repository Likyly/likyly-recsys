#!/usr/bin/env bash
# Installs the tracked hooks from .githooks/ into .git/hooks/ (git's default hooks
# path, not tracked by git itself - each clone needs to run this once).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

for hook in "$REPO_ROOT"/.githooks/*; do
  name="$(basename "$hook")"
  cp "$hook" "$REPO_ROOT/.git/hooks/$name"
  chmod +x "$REPO_ROOT/.git/hooks/$name"
  echo "Installed .git/hooks/$name"
done
