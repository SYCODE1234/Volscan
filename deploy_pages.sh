#!/usr/bin/env bash
# Publish the current dist/volscan.html to https://sycode1234.github.io/Volscan/
#
#   ./deploy_pages.sh
#
# Builds a one-file commit on the orphan gh-pages branch using a temporary
# index, so your working tree and main's history are never touched.
set -euo pipefail
cd "$(dirname "$0")"

[ -f dist/volscan.html ] || { echo "No dist/volscan.html — run build_share.py first." >&2; exit 1; }

THROUGH=$(python3 -c "import json;print(json.load(open('data/scan.json'))['rows'][0]['last_date'])" 2>/dev/null || echo unknown)
BLOB=$(git hash-object -w dist/volscan.html)
EMPTY=$(printf '' | git hash-object -w --stdin)
IDX="$(pwd)/.git/pages.index"; trap 'rm -f "$IDX"' EXIT; rm -f "$IDX"

GIT_INDEX_FILE="$IDX" git read-tree --empty
GIT_INDEX_FILE="$IDX" git update-index --add --cacheinfo 100644,"$BLOB",index.html
GIT_INDEX_FILE="$IDX" git update-index --add --cacheinfo 100644,"$EMPTY",.nojekyll
TREE=$(GIT_INDEX_FILE="$IDX" git write-tree)

PARENT=()
git rev-parse --verify -q gh-pages >/dev/null && PARENT=(-p gh-pages)
COMMIT=$(git commit-tree "$TREE" "${PARENT[@]}" -m "Publish volscan snapshot: prices through $THROUGH")

git branch -f gh-pages "$COMMIT"
git push -q origin gh-pages
echo "Deployed prices through $THROUGH -> https://sycode1234.github.io/Volscan/"
echo "GitHub Pages usually takes under a minute to rebuild."
