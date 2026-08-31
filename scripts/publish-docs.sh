#!/usr/bin/env bash
# Publishes docs/*.html from this repo to github.com/arnoweb/projects-docs,
# under the likyly-recsys/ folder. docs/business-value.html is published as
# that folder's index.html; every other file in docs/ is copied as-is.
#
# Usage:
#   scripts/publish-docs.sh            # clone, copy, commit, push
#   scripts/publish-docs.sh --dry-run  # do everything except the push
set -euo pipefail

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCS_DIR="$REPO_ROOT/docs"
TARGET_REPO="https://github.com/arnoweb/projects-docs.git"
TARGET_SUBDIR="likyly-recsys"

if [[ ! -d "$DOCS_DIR" ]]; then
  echo "No docs/ directory found at $DOCS_DIR - nothing to publish." >&2
  exit 1
fi

if [[ ! -f "$DOCS_DIR/business-value.html" ]]; then
  echo "docs/business-value.html not found - it is required (published as index.html)." >&2
  exit 1
fi

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "Cloning $TARGET_REPO ..."
git clone --depth 1 "$TARGET_REPO" "$WORKDIR/projects-docs" >/dev/null

DEST="$WORKDIR/projects-docs/$TARGET_SUBDIR"
mkdir -p "$DEST"

# business-value.html becomes the published index.html
cp "$DOCS_DIR/business-value.html" "$DEST/index.html"

# every other file/dir under docs/ (architecture.html, assets/, ...) is copied as-is
for item in "$DOCS_DIR"/*; do
  name="$(basename "$item")"
  [[ "$name" == "business-value.html" ]] && continue
  cp -R "$item" "$DEST/$name"
done

cd "$WORKDIR/projects-docs"
git add "$TARGET_SUBDIR"

if git diff --cached --quiet; then
  echo "No changes to publish - $TARGET_SUBDIR/ is already up to date."
  exit 0
fi

echo "Changes to publish:"
git diff --cached --stat -- "$TARGET_SUBDIR"

if $DRY_RUN; then
  echo "--dry-run: skipping commit and push."
  exit 0
fi

SOURCE_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
COMMIT_NAME="$(git -C "$REPO_ROOT" config user.name || echo 'docs-bot')"
COMMIT_EMAIL="$(git -C "$REPO_ROOT" config user.email || echo 'docs-bot@likyly.com')"

git -c user.name="$COMMIT_NAME" -c user.email="$COMMIT_EMAIL" commit \
  -m "Update likyly-recsys docs (from recsys-spotlight-pytorch-fastapi@$SOURCE_SHA)"

git push origin main

echo "Published docs/ -> $TARGET_REPO ($TARGET_SUBDIR/)"
