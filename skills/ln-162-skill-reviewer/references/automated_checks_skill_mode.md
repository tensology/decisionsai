# Automated Verification Checks (SKILL Mode)

<!-- DO NOT add here: Workflow phases -> ln-162-skill-reviewer SKILL.md -->

Run ALL checks below for every SKILL.md in scope. Every FAIL is a confirmed violation -- no judgment needed, no skipping.

## Frontmatter check (D7)
```bash
for f in {scoped SKILL.md files}; do
  head -5 "$f" | grep -q "^---" || echo "FAIL: no frontmatter: $f"
  grep -q "^name:" "$f" || echo "FAIL: no name: $f"
  grep -q "^description:" "$f" || echo "FAIL: no description: $f"
done
```

## Version/date check (D7)
```bash
for f in {scoped SKILL.md files}; do
  grep -q "\*\*Version:\*\*" "$f" || echo "FAIL: no version: $f"
  grep -q "\*\*Last Updated:\*\*" "$f" || echo "FAIL: no date: $f"
  grep -q "\*\*Changes:\*\*" "$f" && echo "FAIL: has Changes section: $f"
done
```

## Size check (D8)
```bash
for f in {scoped SKILL.md files}; do
  lines=$(wc -l < "$f")
  [ "$lines" -gt 800 ] && echo "FAIL: $lines lines (>800): $f"
done
```

## Description length check (D8)
```bash
for f in {scoped SKILL.md files}; do
  desc=$(sed -n '/^description:/p' "$f" | sed 's/^description: *//' | tr -d '"')
  len=${#desc}
  [ "$len" -gt 200 ] && echo "FAIL: description $len chars (>200): $f"
done
```

## MANDATORY READ path verification (D2)
```bash
for f in {scoped SKILL.md files}; do
  dir=$(dirname "$f")
  grep "MANDATORY READ" "$f" | tr '`' '\n' | grep -E '\.(md|json|txt|yaml|sh)$' | grep -v '{' | sort -u | while read path; do
    [ -f "$dir/$path" ] || [ -f "$path" ] || echo "FAIL: missing MANDATORY READ target: $path (from $f)"
  done
done
```

## Orphan references check (D7)
```bash
for f in {scoped SKILL.md files}; do
  dir=$(dirname "$f")
  if [ -d "$dir/references" ]; then
    find "$dir/references" -type f | while read ref; do
      base=$(basename "$ref")
      [[ "$base" == .* ]] && continue
      grep -q "$base" "$f" || echo "FAIL: orphan reference: $ref (not in $f)"
    done
  fi
done
```

## Passive file reference check (D2)
```bash
for f in {scoped SKILL.md files}; do
  # Pattern 1: See/Per/Follows prose patterns
  grep -nE '(^See |^Per |^Follows |See \[).*\.(md|txt|yaml)' "$f" | grep -v "MANDATORY READ" && echo "WARN: passive prose ref in $f"
  # Pattern 2: Markdown links to local files [text](path.md)
  grep -nE '\[[^\]]*\]\([^)]*\.(md|txt|yaml)\)' "$f" | grep -vE '(MANDATORY READ|https?://)' && echo "WARN: passive markdown link in $f"
done
```

## Marketplace skill path check (D8, optional)
```bash
if [ -f .claude-plugin/marketplace.json ]; then
  grep -oE '"\.\/ln-[^"]+' .claude-plugin/marketplace.json | tr -d '"' | while read path; do
    [ ! -d "$path" ] && echo "FAIL: marketplace.json references missing dir: $path"
  done
fi
```

## Root docs stale skill name check (D6)
```bash
for doc in README.md AGENTS.md .claude-plugin/marketplace.json; do
  [ -f "$doc" ] || continue
  grep -oE 'ln-[0-9]+-[a-z-]+' "$doc" | sort -u | while read skill; do
    ls -d ${skill}*/ >/dev/null 2>&1 || echo "FAIL: $doc references missing skill: $skill"
  done
done
```

## Definition of Done check (D7)
```bash
for f in {scoped SKILL.md files}; do
  grep -q "## Definition of Done" "$f" || echo "FAIL: no Definition of Done: $f"
  # Verify checkbox format (- [ ]) if DoD section exists
  if grep -q "## Definition of Done" "$f"; then
    count=$(sed -n '/## Definition of Done/,/^---/p' "$f" | grep -c '^\- \[ \]')
    [ "$count" -eq 0 ] && echo "FAIL: DoD has no checkbox items (- [ ]): $f"
  fi
done
```

## Meta-Analysis check (D7)
```bash
for f in {scoped SKILL.md files}; do
  # Detect skill level from Type line
  level=$(grep -oE 'L[12]' "$f" | head -1)
  if [ -n "$level" ]; then
    grep -q "Meta-Analysis" "$f" || echo "FAIL: L1/L2 skill missing Meta-Analysis: $f"
    grep -q "meta_analysis_protocol" "$f" || echo "FAIL: L1/L2 skill missing meta_analysis_protocol MANDATORY READ: $f"
  fi
done
```

## Publishing skill requirements check (D7)
```bash
for f in {scoped SKILL.md files}; do
  if grep -qE '(gh api graphql.*mutation|gh issue comment)' "$f"; then
    grep -qi "fact.check" "$f" || echo "FAIL: publishing skill missing Fact-Check phase: $f"
    grep -q "humanizer_checklist" "$f" || echo "FAIL: publishing skill missing humanizer_checklist MANDATORY READ: $f"
  fi
done
```

## Skill count accuracy check (D8)
```bash
actual=$(ls -d ln-*/SKILL.md 2>/dev/null | wc -l)
if [ -f README.md ]; then
  badge=$(grep -oE 'skills-[0-9]+' README.md | grep -oE '[0-9]+')
  [ -n "$badge" ] && [ "$badge" != "$actual" ] && echo "FAIL: README badge says $badge, actual $actual"
fi
if [ -f .claude-plugin/marketplace.json ]; then
  market=$(grep -oE '"\.\/ln-[^"]+' .claude-plugin/marketplace.json | wc -l)
  [ "$market" != "$actual" ] && echo "FAIL: marketplace.json has $market entries, actual $actual"
fi
```
