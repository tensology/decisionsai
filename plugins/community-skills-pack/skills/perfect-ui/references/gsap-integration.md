# GSAP Skill Integration

When motion intensity hits 3/3 OR user brief contains GSAP-specific keywords, perfect-ui triggers official gsap-* skills (installed at `~/.claude/skills/gsap-*`) for implementation. Falls back to inline patterns if skills not installed — preserves v2.2.0 self-contained behavior.

## When this applies (auto-detect at Phase 7)

Triggers (any one fires):

1. **Motion intensity = 3/3** (from Phase 2e) — choreographed motion typically needs GSAP
2. **User brief / inspirations contain keywords:** `GSAP` · `ScrollTrigger` · `scroll choreography` · `scrub` · `pin section` · `scroll-driven` · `scroll-linked`
3. **Phase 2d effect choice = "Scroll-driven distortion"** (requires Lenis + GSAP ScrollTrigger)

Auto-detection runs at Phase 7 entry (see `workflow-implement.md § Step 2.5 — GSAP integration check`).

## Skill detection mechanism

Before invoking, check skills exist:

```bash
ls ~/.claude/skills/gsap-core/SKILL.md 2>/dev/null
```

- If file exists → skills installed, proceed with active Skill tool call.
- If missing → **suggest install once, then fall back** (see § Lazy install suggestion below).

## Lazy install suggestion

When `gsap_needed = true` AND skills NOT installed, emit a **one-time, non-blocking** suggestion to the user at Phase 7 entry — BEFORE pre-emit verification, BEFORE code emission.

**Suggestion message template:**

```
This page locks motion intensity 3/3 (or your brief mentions GSAP/ScrollTrigger).
For richer scroll choreography, the official GSAP skills are available:

    npx skills add greensock/gsap-skills

Installs 8 skills: gsap-core, gsap-scrolltrigger, gsap-react, gsap-timeline,
gsap-plugins, gsap-performance, gsap-frameworks, gsap-utils.

Skip if you prefer inline patterns — perfect-ui falls back automatically.
Proceed with inline fallback? [y/N to install first]
```

**Rules:**

1. **One-time per project** — never re-prompt within the same brainstorm → plan → implement cycle. Log decision in `plans/{slug}/plan.md § GSAP decision` (`installed` / `declined-fallback` / `skipped-no-need`).
2. **Non-blocking** — user declining = proceed with inline fallback. Do NOT halt the pipeline.
3. **Component-scope exception** — component-scope (see `component-scope.md`) skips the suggestion entirely; component artifacts use minimal motion, inline patterns sufficient.
4. **Skip when not needed** — if motion intensity 0-2/3 AND no keyword match AND Phase 2d effect ≠ "Scroll-driven distortion", do NOT suggest. Lazy install respects YAGNI.
5. **Re-detect after install** — if user runs the install command, re-check `~/.claude/skills/gsap-core/SKILL.md` before proceeding to confirm success.

**Why lazy, not bundled at install time:**
- GSAP skills are independent (useful beyond perfect-ui) — bundling forces 8 extra skills on users who never use motion 3/3
- Respects v2.4.1 design: "Skills are OPTIONAL: perfect-ui falls back to inline GSAP patterns if skills not installed"
- `npx skills` CLI does not currently support `depends_on` (see vercel-labs/skills#515 — open feature request, no ETA)

## The 8 gsap skills (selection logic)

Always invoke `gsap-core` (foundation). Add others based on use case:

| Trigger condition | Skills to invoke |
|-------------------|------------------|
| Default (any GSAP use) | `gsap-core` |
| React / Next.js project (from pre-flight scan or stack lock) | + `gsap-react` |
| Vue / Svelte / SvelteKit | + `gsap-frameworks` |
| Scroll-driven animation (Phase 2d = "scroll-driven distortion" OR keyword "ScrollTrigger" / "scrub" / "pin") | + `gsap-scrolltrigger` |
| Multi-step sequencing (timelines, choreography) | + `gsap-timeline` |
| Plugins needed (Flip / Draggable / SplitText / MorphSVG / DrawSVG / Inertia / Observer / Custom*) | + `gsap-plugins` |
| Performance review / <60fps issue | + `gsap-performance` |
| Math / array / value mapping helpers (clamp / mapRange / random / snap) | + `gsap-utils` |

**Typical bundle** for GSAP-intensive landing (Next.js + scroll + timeline):
- `gsap-core` + `gsap-react` + `gsap-scrolltrigger` + `gsap-timeline` (4 skills)

## Active Skill tool invocation pattern

Sequential calls per use case (not single batch). Each skill loads patterns + best practices that inform Phase 7 implementation.

Pseudo-code:

```
if gsap_needed:
    if skills_installed:
        Skill(skill="gsap-core", args=f"implement GSAP for {brief.summary} at intensity 3/3, personality {motion_personality}")
        if framework in ("react", "nextjs"):
            Skill(skill="gsap-react", args="useGSAP hook setup + cleanup pattern")
        elif framework in ("vue", "svelte"):
            Skill(skill="gsap-frameworks", args=f"{framework} lifecycle + cleanup for GSAP")
        if scroll_animation_needed:
            Skill(skill="gsap-scrolltrigger", args="ScrollTrigger setup for {scroll_pattern}")
        if timeline_needed:
            Skill(skill="gsap-timeline", args="timeline chain for {sequence_description}")
        if plugins_needed:
            Skill(skill="gsap-plugins", args=f"register and use {plugin_list}")
        # gsap-performance only if existing issue
        # gsap-utils only if utility functions needed
    else:
        # Fall back to inline patterns below
        use_inline_fallback()
```

## Fallback inline patterns (skills not installed)

For users without gsap-skills, use these minimal patterns directly. For deeper guidance, recommend user installs `~/.claude/skills/gsap-*`.

### useGSAP hook (React / Next.js)
```tsx
import { useGSAP } from '@gsap/react';
import { gsap } from 'gsap';
import { useRef } from 'react';

const containerRef = useRef<HTMLDivElement>(null);

useGSAP(() => {
  gsap.to(".target", { x: 100, duration: 1 });
}, { scope: containerRef });
```

Cleanup is automatic via `useGSAP` hook scope. Install: `npm install gsap @gsap/react`.

### ScrollTrigger basic setup
```tsx
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
gsap.registerPlugin(ScrollTrigger);

gsap.to(".target", {
  x: 100,
  scrollTrigger: {
    trigger: ".target",
    start: "top center",
    end: "bottom center",
    scrub: 1,
  },
});
```

### Timeline chain
```tsx
const tl = gsap.timeline({ defaults: { duration: 0.6, ease: "power2.out" } });
tl.from(".a", { opacity: 0, y: 20 })
  .from(".b", { opacity: 0, y: 20 }, "-=0.4")
  .from(".c", { opacity: 0, y: 20 }, "-=0.4");
```

For React cleanup edge cases, Vue/Svelte lifecycle specifics, plugin registration, performance optimization, and utility helpers — install the gsap-* skills.

## Anti-slop GSAP-specific checks (Phase 8 audit)

Run at Phase 8 (see `workflow-audit.md § Step F.5 — GSAP-specific checks`):

1. **GSAP imported but unused / underused (<3 use cases)** — Tier 2 bundle bloat. Either commit to GSAP or remove and use Framer Motion / CSS.
2. **GSAP imported but motion intensity locked at 0-1/3** — Tier 2 mismatch. GSAP is for intensity 3/3; lower intensities should use CSS or Framer Motion. Indicates personality / intensity confusion.
3. **`window.addEventListener('scroll')` for scroll animation when ScrollTrigger available** — Tier 2 performance violation. Use ScrollTrigger instead (handles RAF batching, prevents reflow thrash).

## Extensibility

Architecture supports future addition of other animation library skills when official skills become available. Current state: only GSAP populated (8 skills). Future patches replicate this model:

- `references/{library}-integration.md` — detection + selection + fallback
- `workflow-implement.md` — detection step references the integration file
- `workflow-audit.md` — library-specific anti-slop checks
- Fallback patterns preserve self-contained behavior

**Examples for future** (when official skills exist):
- **Framer Motion** — detection = intensity 2-3/3, install check = `~/.claude/skills/framer-motion-*`
- **Lenis** — detection = Phase 2d "cursor-reactive scroll" OR keyword "smooth scroll"
- **Lottie** — detection = vibe = Playful + complex character motion + Phase 4 illustration style = "Lottie animation"

## Cross-references

- `motion-patterns.md § Easing Library` + `§ Motion Personalities` — intensity 3/3 personality drives skill selection
- `workflow-implement.md § Step 2.5` — Phase 7 detection step
- `workflow-audit.md § Step F.5` — Phase 8 GSAP-specific anti-slop checks
- `preflight-scan.md` — framework detection feeds skill selection (React vs Vue/Svelte vs vanilla)
- 8 installed gsap skills: `~/.claude/skills/gsap-{core,scrolltrigger,react,timeline,plugins,performance,frameworks,utils}/`
