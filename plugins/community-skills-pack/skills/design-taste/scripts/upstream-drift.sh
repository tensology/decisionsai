#!/usr/bin/env bash
# scripts/upstream-drift.sh
#
# Report-only: checks the five upstream files this skill ported or copied
# from against the pinned hashes in scripts/upstream-snapshot.txt, and
# prints OK / DRIFT / MISSING / UNREACHABLE per file. Never fails the build:
# a 404 (a file that moved or was restructured upstream, e.g. impeccable's
# post-restructure reference/interaction-design.md) is a MISSING report, and
# a network failure (offline, DNS, timeout) is UNREACHABLE rather than a
# false MISSING. Always exits 0.
#
# Usage:
#   bash scripts/upstream-drift.sh              # report drift
#   bash scripts/upstream-drift.sh --snapshot    # regenerate the snapshot
#                                                 # (redirect to scripts/upstream-snapshot.txt)

set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAPSHOT="$DIR/upstream-snapshot.txt"
MODE="${1:-report}"

# One tracked upstream file per line: "<owner/repo> <path-in-repo>".
TRACKED="emilkowalski/skill skills/emil-design-eng/SKILL.md
pbakaus/impeccable .agent/skills/impeccable/SKILL.md
pbakaus/impeccable .agent/skills/impeccable/reference/craft-floor.md
pbakaus/impeccable .agent/skills/impeccable/reference/operate.md
leonxlnx/taste-skill skills/taste-skill/SKILL.md"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi
}

# Prints the sha256 of the upstream file, or MISSING (HTTP 404/410: the file
# moved or was removed upstream), or UNREACHABLE (no connection, timeout, or
# any other HTTP status). Branches on the HTTP status, not curl's exit code:
# GitHub's CDN answers some 404s over HTTP/2 in a way that makes curl exit 56
# instead of 22, so the exit code alone cannot tell a 404 from a dropped link.
hash_of() {
  local repo="$1" path="$2" tmp code
  tmp="$(mktemp)"
  code="$(curl -sSL --max-time 20 -o "$tmp" -w '%{http_code}' \
    "https://raw.githubusercontent.com/$repo/HEAD/$path" 2>/dev/null)"
  case "$code" in
    200) if [ -s "$tmp" ]; then sha256 < "$tmp" | awk '{print $1}'; else echo MISSING; fi ;;
    404|410) echo MISSING ;;
    *) echo UNREACHABLE ;;
  esac
  rm -f "$tmp"
}

baseline_for() {
  local repo="$1" path="$2"
  [ -f "$SNAPSHOT" ] || return 0
  awk -v r="$repo" -v p="$path" '$1 == r && $2 == p { print $3 }' "$SNAPSHOT"
}

if [ "$MODE" = "--snapshot" ]; then
  while read -r repo path; do
    [ -z "$repo" ] && continue
    echo "$repo $path $(hash_of "$repo" "$path")"
  done <<< "$TRACKED"
  exit 0
fi

while read -r repo path; do
  [ -z "$repo" ] && continue
  baseline="$(baseline_for "$repo" "$path")"
  current="$(hash_of "$repo" "$path")"
  case "$current" in
    MISSING|UNREACHABLE) status="$current" ;;
    "$baseline") status="OK" ;;
    *) status="DRIFT" ;;
  esac
  printf '%-8s %s/%s\n' "$status" "$repo" "$path"
done <<< "$TRACKED"

exit 0
