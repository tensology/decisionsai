#!/usr/bin/env bash
# Server-side: sync CHANGELOG.md from github.com/tensology/decisionsai and rebuild the site.
# Installed on tensology.com at /var/www/decisionsai.net/scripts/deploy_changelog.sh
#
# Usage (on server):
#   ./scripts/deploy_changelog.sh [git-ref]
# Default ref: main

set -euo pipefail

SITE_ROOT="/var/www/decisionsai.net"
REF="${1:-main}"
DEST="${SITE_ROOT}/frontend/public/CHANGELOG.md"
RAW_URL="https://raw.githubusercontent.com/tensology/decisionsai/${REF}/CHANGELOG.md"

if [[ ! -d "${SITE_ROOT}/frontend" ]]; then
  echo "Site root not found: ${SITE_ROOT}" >&2
  exit 1
fi

echo "Fetching ${RAW_URL}"
curl -fsSL "$RAW_URL" -o "$DEST"
echo "Wrote $(wc -c <"$DEST") bytes to ${DEST}"

echo "Building frontend..."
cd "${SITE_ROOT}/frontend"
npm run build

if git -C "$SITE_ROOT" status --porcelain -- frontend/public/CHANGELOG.md | grep -q .; then
  git -C "$SITE_ROOT" add frontend/public/CHANGELOG.md
  git -C "$SITE_ROOT" commit -m "Update public CHANGELOG from decisionsai ${REF}"
  if git -C "$SITE_ROOT" push origin HEAD; then
    echo "Committed and pushed website repo."
  else
    echo "Committed locally (push skipped or failed)."
  fi
else
  echo "No CHANGELOG content change; skipped website git commit."
fi

echo "Done. https://www.decisionsai.net/changelog"
