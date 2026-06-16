#!/usr/bin/env bash
# Publish CHANGELOG.md to https://www.decisionsai.net/changelog
#
# Usage:
#   ./scripts/deploy_changelog_to_site.sh           # deploy origin/main (must be pushed)
#   ./scripts/deploy_changelog_to_site.sh <ref>   # deploy explicit commit or branch on GitHub
#
# Environment overrides:
#   DECISIONS_SITE_SSH   default root@tensology.com
#   DECISIONS_SITE_ROOT  default /var/www/decisionsai.net

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SSH_HOST="${DECISIONS_SITE_SSH:-root@tensology.com}"
SITE_ROOT="${DECISIONS_SITE_ROOT:-/var/www/decisionsai.net}"
REF="${1:-}"

if [[ -z "$REF" ]]; then
  if ! git rev-parse --verify origin/main >/dev/null 2>&1; then
    git fetch origin main
  fi
  LOCAL_SHA="$(git rev-parse main)"
  REMOTE_SHA="$(git rev-parse origin/main)"
  if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
    echo "main is ahead of or behind origin/main." >&2
    echo "Push first:  git push origin main" >&2
    echo "Or deploy a specific ref:  $0 <commit-sha>" >&2
    exit 1
  fi
  REF="$REMOTE_SHA"
fi

if [[ ! -f "$REPO_ROOT/CHANGELOG.md" ]]; then
  echo "CHANGELOG.md not found in $REPO_ROOT" >&2
  exit 1
fi

echo "Deploying CHANGELOG ref ${REF} to ${SSH_HOST}:${SITE_ROOT} ..."
ssh "$SSH_HOST" "${SITE_ROOT}/scripts/deploy_changelog.sh" "$REF"
echo "Live: https://www.decisionsai.net/changelog"
