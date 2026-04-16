#!/usr/bin/env bash
# Automated SKILL.md verification checks (ln-162-skill-reviewer Phase 2)
# Usage: bash run_checks.sh <SKILL.md files...>
# Example: bash run_checks.sh ln-005-*/SKILL.md ln-810-*/SKILL.md
#
# Exit code: 0 if all pass, 1 if any FAIL found
# Lessons learned (bugs fixed vs template version):
#   - grep -q returns exit code 1 on no match -> breaks && chains; use if/then
#   - grep -c returns exit code 1 when count=0 -> append || true
#   - passive ref grep needs || true to avoid false script failure
#   - MANDATORY READ paths to target project files (docs/project/*) are
#     runtime-only and cannot be verified from skills repo -> not false positives
#     but expected; the script still reports them for transparency
#   - pipe | while read creates subshell -> FAILS counter lost; use < <(...) instead

set -uo pipefail

SCOPE="$@"
FAILS=0

if [ $# -eq 0 ]; then
  echo "Usage: bash run_checks.sh <SKILL.md files...>"
  echo "Example: bash run_checks.sh ln-*/SKILL.md"
  exit 2
fi

fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }
warn() { echo "WARN: $1"; }

# ── CHECK 1: Frontmatter (D7) ──────────────────────────────────────
echo "=== CHECK 1: Frontmatter (D7) ==="
for f in $SCOPE; do
  head -5 "$f" | grep -q "^---" || fail "no frontmatter: $f"
  grep -q "^name:" "$f" || fail "no name: $f"
  grep -q "^description:" "$f" || fail "no description: $f"
done
echo "DONE"
echo ""

# ── CHECK 2: Version/Date (D7) ─────────────────────────────────────
echo "=== CHECK 2: Version/Date (D7) ==="
for f in $SCOPE; do
  grep -q '\*\*Version:\*\*' "$f" || fail "no version: $f"
  grep -q '\*\*Last Updated:\*\*' "$f" || fail "no date: $f"
  if grep -q '\*\*Changes:\*\*' "$f"; then fail "has Changes section: $f"; fi
done
echo "DONE"
echo ""

# ── CHECK 3: Size <=800 (D8) ───────────────────────────────────────
echo "=== CHECK 3: Size <=800 (D8) ==="
for f in $SCOPE; do
  lines=$(wc -l < "$f")
  if [ "$lines" -gt 800 ]; then fail "$lines lines (>800): $f"; fi
done
echo "DONE"
echo ""

# ── CHECK 4: Description <=200 chars (D8) ──────────────────────────
echo "=== CHECK 4: Description <=200 chars (D8) ==="
for f in $SCOPE; do
  desc=$(sed -n '/^description:/p' "$f" | sed 's/^description: *//' | tr -d '"')
  len=${#desc}
  if [ "$len" -gt 200 ]; then fail "description $len chars (>200): $f"; fi
done
echo "DONE"
echo ""

# ── CHECK 5: MANDATORY READ paths (D2) ─────────────────────────────
echo "=== CHECK 5: MANDATORY READ paths (D2) ==="
for f in $SCOPE; do
  dir=$(dirname "$f")
  while read -r path; do
    if [ ! -f "$dir/$path" ] && [ ! -f "$path" ]; then
      fail "missing MANDATORY READ target: $path (from $f)"
    fi
  done < <(grep "MANDATORY READ" "$f" | tr '`' '\n' | grep -E '\.(md|json|txt|yaml|sh)$' | grep -v '{' | sort -u)
done
echo "DONE"
echo ""

# ── CHECK 6: Orphan references (D7) ────────────────────────────────
echo "=== CHECK 6: Orphan references (D7) ==="
for f in $SCOPE; do
  dir=$(dirname "$f")
  if [ -d "$dir/references" ]; then
    while read -r ref; do
      base=$(basename "$ref")
      [[ "$base" == .* ]] && continue
      grep -q "$base" "$f" || fail "orphan reference: $ref (not in $f)"
    done < <(find "$dir/references" -type f)
  fi
done
echo "DONE"
echo ""

# ── CHECK 7: Passive file refs (D2) ────────────────────────────────
echo "=== CHECK 7: Passive file refs (D2) ==="
for f in $SCOPE; do
  result1=$(grep -nE '(^See |^Per |^Follows |See \[).*\.(md|txt|yaml)' "$f" | grep -v "MANDATORY READ" || true)
  result2=$(grep -nE '\[[^\]]*\]\([^)]*\.(md|txt|yaml)\)' "$f" | grep -vE '(MANDATORY READ|https?://)' || true)
  if [ -n "$result1" ]; then warn "passive prose ref in $f:"; echo "$result1"; fi
  if [ -n "$result2" ]; then warn "passive markdown link in $f:"; echo "$result2"; fi
done
echo "DONE"
echo ""

# ── CHECK 8: DoD with checkboxes (D7) ──────────────────────────────
echo "=== CHECK 8: DoD with checkboxes (D7) ==="
for f in $SCOPE; do
  grep -q "## Definition of Done" "$f" || fail "no Definition of Done: $f"
  if grep -q "## Definition of Done" "$f"; then
    count=$(sed -n '/## Definition of Done/,/^---/p' "$f" | grep -c '^\- \[ \]' || true)
    if [ "$count" -eq 0 ]; then fail "DoD has no checkbox items (- [ ]): $f"; fi
  fi
done
echo "DONE"
echo ""

# ── CHECK 9: Meta-Analysis L1/L2 (D7) ──────────────────────────────
echo "=== CHECK 9: Meta-Analysis L1/L2 (D7) ==="
for f in $SCOPE; do
  level=$(grep -oE 'L[12]' "$f" | head -1 || true)
  if [ -n "$level" ]; then
    grep -q "Meta-Analysis" "$f" || fail "L1/L2 skill missing Meta-Analysis: $f"
    grep -q "meta_analysis_protocol" "$f" || fail "L1/L2 skill missing meta_analysis_protocol ref: $f"
  fi
done
echo "DONE"
echo ""

# ── CHECK 10: Publishing skills (D7) ───────────────────────────────
echo "=== CHECK 10: Publishing skills (D7) ==="
for f in $SCOPE; do
  if grep -qE '(gh api graphql.*mutation|gh issue comment)' "$f"; then
    grep -qi "fact.check" "$f" || fail "publishing skill missing Fact-Check: $f"
    grep -q "humanizer_checklist" "$f" || fail "publishing skill missing humanizer_checklist ref: $f"
  fi
done
echo "DONE"
echo ""

# ── CHECK 11: Marketplace paths (D8, optional) ─────────────────────
echo "=== CHECK 11: Marketplace paths (D8) ==="
if [ -f .claude-plugin/marketplace.json ]; then
  while read -r path; do
    if [ ! -d "$path" ]; then fail "marketplace.json references missing dir: $path"; fi
  done < <(grep -oE '"\.\/ln-[^"]+' .claude-plugin/marketplace.json | tr -d '"')
else
  echo "SKIP: no marketplace.json"
fi
echo "DONE"
echo ""

# ── CHECK 12: Root docs stale names (D6) ───────────────────────────
echo "=== CHECK 12: Root docs stale names (D6) ==="
for doc in README.md .claude-plugin/marketplace.json; do
  if [ -f "$doc" ]; then
    while read -r skill; do
      ls -d ${skill}*/ >/dev/null 2>&1 || fail "$doc references missing skill: $skill"
    done < <(grep -oE 'ln-[0-9]+-[a-z-]+' "$doc" | sort -u)
  fi
done
echo "DONE"
echo ""

# ── CHECK 13: Skill count accuracy (D8) ────────────────────────────
echo "=== CHECK 13: Skill count accuracy (D8) ==="
actual=$(ls -d ln-*/SKILL.md 2>/dev/null | wc -l)
echo "Actual skill count: $actual"
if [ -f README.md ]; then
  badge=$(grep -oE 'skills-[0-9]+' README.md | grep -oE '[0-9]+' || true)
  if [ -n "$badge" ]; then
    echo "README badge: $badge"
    if [ "$badge" != "$actual" ]; then fail "README badge says $badge, actual $actual"; fi
  fi
fi
if [ -f .claude-plugin/marketplace.json ]; then
  market=$(grep -oE '"\.\/ln-[^"]+' .claude-plugin/marketplace.json | wc -l)
  echo "Marketplace entries: $market"
  if [ "$market" != "$actual" ]; then fail "marketplace.json has $market entries, actual $actual"; fi
fi
echo "DONE"
echo ""

# ── SUMMARY ─────────────────────────────────────────────────────────
echo "================================"
if [ "$FAILS" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
  exit 0
else
  echo "TOTAL FAILURES: $FAILS"
  exit 1
fi
