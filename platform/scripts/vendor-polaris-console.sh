#!/usr/bin/env bash
# Re-vendor platform/helm/vendor/polaris-console/ from apache/polaris-tools.
#
#   platform/scripts/vendor-polaris-console.sh [ref]
#
# `ref` defaults to the pin recorded in vendor/polaris-console/VENDORED.md.
# After running: bump POLARIS_TOOLS_REF in .github/workflows/build-polaris-console.yml
# and image.tag in platform/helm/values/polaris-console.yaml to the same ref, then
# run that workflow to publish the matching image.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
dest="$here/helm/vendor/polaris-console"
ref="${1:-$(grep -oE '[0-9a-f]{40}' "$dest/VENDORED.md" | head -1)}"
[[ -n "$ref" ]] || { echo "no ref given and none found in VENDORED.md" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git clone --quiet --depth 1 --filter=blob:none --sparse \
  https://github.com/apache/polaris-tools.git "$tmp/pt"
git -C "$tmp/pt" sparse-checkout set console/helm >/dev/null
git -C "$tmp/pt" fetch --quiet --depth 1 origin "$ref"
git -C "$tmp/pt" checkout --quiet FETCH_HEAD

cp "$dest/VENDORED.md" "$tmp/VENDORED.md"
rm -rf "$dest"
cp -R "$tmp/pt/console/helm" "$dest"
rm -rf "$dest/tests" "$dest/templates/tests" "$dest/ci"
sed -E "s/[0-9a-f]{40}/$ref/" "$tmp/VENDORED.md" > "$dest/VENDORED.md"

echo "vendored console/helm @ $ref -> $dest"
echo "now bump POLARIS_TOOLS_REF and values/polaris-console.yaml image.tag to $ref"
