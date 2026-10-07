# Redesign Audit Checklist

When mode = redesign, run this audit BEFORE Phase 1. Output: `plans/{date}-{slug}/audit.md`.

## Inputs Needed from User

Use `AskUserQuestion` to collect:

1. **URL or screenshot of current landing** (required)
2. **What's the conversion goal?** (might differ from old)
3. **Why redesign now?** (new market position, lost conversions, brand refresh, etc.)
4. **Anything sacred** (must-keep elements, copy, proof points)
5. **Rough timeline** (affects scope)

## Audit Steps

### Step 1 — Capture current state
Use a vision-capable model to analyze the provided URL/screenshot:

```
prompt: |
  Analyze this landing page. Output:
  - Current vibe in one sentence
  - Color palette (extract 5 dominant hex codes)
  - Typography (identify display + body fonts if recognizable)
  - Layout structure (list sections in order)
  - Tone of copy (formal/casual/aggressive/quiet)
  - Icon style (library identifiable? custom? emoji?)
  - 3D / motion presence
  - 3 strongest moments
  - 3 weakest moments
  - Likely target audience based on visual signals
input: current-landing-screenshot.png
```

Save output to `plans/{date}-{slug}/audit-current-state.md`.

### Step 2 — Identify what's working
Extract from analysis:
- **Vibe equity** — is the current vibe known/loved by their users?
- **Conversion-proven elements** — which sections likely drive the existing CTA?
- **Brand markers** — logo, brand voice, signature color worth preserving

Document 3 things to KEEP. Be specific:
> Keep: the warm cream background — it's a known brand marker
> Keep: the testimonial format with full quote + photo + role
> Keep: the asymmetric hero composition — distinctive

### Step 3 — Identify what to kill
List 5 elements to REMOVE. Each with reason:
> Kill: 3-column equal feature cards (generic AI layout)
> Kill: stock photos in testimonials (look fake)
> Kill: purple-blue gradient CTA (AI fingerprint)
> Kill: 12-feature grid (paralysis, dilution)
> Kill: lucide-react icon set (no cohesion with brand vibe)

### Step 4 — Audit anti-slop violations
Run anti-slop checklist against current site:
```bash
# If you have access to the source repo
grep -rE 'lucide-react|@heroicons|phosphor' app/  # forbidden libs
grep -rE 'Inter|Roboto' app/  # forbidden fonts
grep -rEi 'elevate|seamless|unleash' app/  # AI clichés
```

If only screenshot, use a vision-capable model to detect visually:
```
"Looking at this landing, identify any of these AI-default patterns:
- Inter or Roboto typography
- Purple/blue gradient hero
- 3-column equal feature cards
- Generic library icons (Lucide, Heroicons)
- AI cliché copy (Elevate / Seamless / Unleash)
- Stock-photo testimonial avatars
- Centered hero with centered H1"
```

Document violations to fix.

### Step 5 — Determine redesign scope

Use `AskUserQuestion` with header "Redesign Scope":

| Scope | What changes |
|-------|--------------|
| Refresh | Same vibe, fix anti-slop, refine type/color, replace icons |
| Repositioning | New vibe (different anchor in `visual-direction-guide.md`), keep proof |
| Full rebuild | Everything new — vibe, copy, layout, assets |

Scope drives Phase 1's brief depth.

## Audit Output Template

Save to `plans/{date}-{slug}/audit.md`:

```markdown
# Redesign Audit — {Project Name}

## Current State
- Vibe (one sentence): {from vision-capable model analysis}
- Palette: {5 hex codes}
- Typography: {display + body if identifiable}
- Layout: {section list in order}
- Tone: {formal | casual | aggressive | quiet}
- Icons: {library identified | custom | emoji}
- 3D / motion: {present description | none}

## Conversion Context
- Original goal: {signup | demo | buy | waitlist}
- New goal: {same | different — describe}
- Why redesign: {user's stated reason}

## Keep (3 elements)
1. {element} — {why it works}
2. {element} — {why it works}
3. {element} — {why it works}

## Kill (5 elements)
1. {element} — {why it fails}
2. {element} — {why it fails}
3. {element} — {why it fails}
4. {element} — {why it fails}
5. {element} — {why it fails}

## Anti-Slop Violations Detected
- [ ] {violation 1}
- [ ] {violation 2}
- [ ] {violation 3}

## Redesign Scope
{Refresh | Repositioning | Full rebuild}

## Constraints
- Sacred elements (must keep): {list}
- Timeline: {user's stated}
- Technical: {existing stack — match or replace}
```

## Hand-off to Phase 1

When entering Phase 1 (inline brainstorm protocol — see `workflow-phases.md` § Phase 1), prepend the audit context:

```
We are redesigning an existing landing page. Audit attached at
plans/{date}-{slug}/audit.md.

Critical context:
- Keep these 3 elements: {list}
- Kill these 5 elements: {list}
- Redesign scope: {scope}

Now run the standard discovery questions (Product / Audience / Conversion goal /
Vibe / Inspirations / Anti-references / Constraints), but factor in the audit
findings. The vibe choice in particular must reconcile with {scope}:
- Refresh → same vibe, sharper execution
- Repositioning → new anchor, justify the shift
- Full rebuild → free choice
```

## Common Redesign Anti-Patterns

When redesigning, avoid:
- **Throwing out brand equity** — if users recognize the warm cream + serif combo, "modernizing" to dark mode + Inter destroys recognition
- **Chasing trends** — glassmorphism in 2024 looked dated by 2026; pick durable choices
- **Performance regressions** — old site at 95 Lighthouse, new at 70 because of 3D = failure regardless of how it looks
- **Copy regression** — old site's specific outcome statements ("cut deploy from 14min to 2min") replaced with vague "Elevate your workflow"
- **Removing all proof** — testimonials and logo bars are conversion-load-bearing; redesign them, don't delete
