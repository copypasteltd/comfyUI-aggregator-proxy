#!/usr/bin/env bash
# Repository-owned Skill release implementation. Called only for an authorized tag.
set -euo pipefail
test "${GITHUB_ACTIONS:-}" = true
test "${GH_REPO:-}" = copypasteltd/comfyUI-aggregator-proxy

set -euo pipefail
version=$(tr -d '\r\n' < app/skills/media-gen-skill/VERSION)
test "$RELEASE_TAG" = "media-gen-skill-v$version"
python3 app/scripts/build-media-gen-skill.py --output app/.local/skill-release
python3 app/scripts/build-media-gen-skill.py --output app/.local/skill-rebuild
cmp "app/.local/skill-release/media-gen-skill-$version.zip" "app/.local/skill-rebuild/media-gen-skill-$version.zip"
(cd app/.local/skill-release && sha256sum --check SHA256SUMS.txt)

set -euo pipefail
version=$(tr -d '\r\n' < app/skills/media-gen-skill/VERSION)
mkdir -p app/.local/skill-download
if gh release view "$RELEASE_TAG" --json isDraft > app/.local/skill-existing-release.json 2> app/.local/skill-release-view.stderr; then
  draft=$(python3 -c 'import json; print(str(json.load(open("app/.local/skill-existing-release.json"))["isDraft"]).lower())')
  if test "$draft" = true; then
    gh release upload "$RELEASE_TAG" app/.local/skill-release/* --clobber
  fi
else
  gh release create "$RELEASE_TAG" app/.local/skill-release/* \
    --verify-tag --draft --title "Media Gen Skill $version" \
    --notes-file "docs/releases/media-gen-skill/$version.md"
fi
gh release download "$RELEASE_TAG" --dir app/.local/skill-download
(cd app/.local/skill-download && sha256sum --check SHA256SUMS.txt)
for asset in "media-gen-skill-$version.zip" SHA256SUMS.txt release.json; do
  cmp "app/.local/skill-release/$asset" "app/.local/skill-download/$asset"
done
gh release edit "$RELEASE_TAG" --draft=false --latest=false
gh release view "$RELEASE_TAG" --json url,tagName,isDraft,assets
