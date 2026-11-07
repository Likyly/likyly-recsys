#!/usr/bin/env bash
# Publishes one SDK folder as its own GitHub repository: Go modules, Swift Package Manager and Packagist
# install from git, so the repository and its version tag are the release.
#
#   sdk/scripts/release-repo.sh go    Likyly/likyly-go    v1.0.0          # assemble, print the folder
#   sdk/scripts/release-repo.sh go    Likyly/likyly-go    v1.0.0 --push   # then commit, tag and push
#
# The repository must exist. Its content is replaced by the SDK folder, plus the shared conformance
# scenarios (the tests read conformance/scenarios.json) and the LICENSE.
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: $0 <sdk folder> <owner/repo> <tag> [--push]" >&2
  exit 2
fi
sdk=$1 repo=$2 tag=$3 push=${4:-}
root=$(cd "$(dirname "$0")/.." && pwd)
src="$root/$sdk"
[ -d "$src" ] || { echo "no such SDK folder: $src" >&2; exit 1; }

work=$(mktemp -d)
if ! git clone -q "https://github.com/$repo.git" "$work/repo" 2>/dev/null; then
  [ "$push" = "--push" ] && { echo "create https://github.com/$repo first" >&2; exit 1; }
  git init -q "$work/repo"
fi
cd "$work/repo"
git checkout -q -B main
git rm -rqf --ignore-unmatch . >/dev/null

rsync -a \
  --exclude .build --exclude .swiftpm --exclude Package.resolved \
  --exclude vendor --exclude node_modules --exclude composer.lock \
  --exclude .phpunit.result.cache --exclude .phpunit.cache --exclude .DS_Store \
  "$src/" ./
mkdir -p conformance
cp "$root/conformance/scenarios.json" conformance/scenarios.json
cp "$root/LICENSE" LICENSE

git add -A
if [ "$push" != "--push" ]; then
  echo "$work/repo"
  exit 0
fi

if git diff --cached --quiet; then
  echo "nothing changed since the last release" >&2
else
  git commit -qm "$tag"
fi
git tag -a "$tag" -m "$tag"
git push -q origin main "$tag"
echo "pushed $repo $tag"
