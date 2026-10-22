---
name: decisions-visual-plan
description: Route technical planning and review between Decisions Mermaid viewer, BuilderIO visual-plan/recap skills, and Open Design when the work needs richer artifacts.
---

<!-- GLOBAL_UI_UX_REFERENCE -->
## Global interface source

Before using this skill for interface work, read the current global `ui-ux-system` skill (`~/.agents/skills/ui-ux-system/SKILL.md`), especially its Paul's rules, settled taste rules, and Definition of Done - UI. Those rules are authoritative and override conflicting guidance here. Aesthetic recipes are opt-in: use them only when the brief explicitly calls for them and they earn their place. This skill adds only its specialized guidance.

# Decisions Visual Planning

**I am picking the right visual surface so the user can review structure before code ships — not dumping prose in chat.**

## Choose the surface

| User job | Use | How |
|----------|-----|-----|
| Quick architecture / ER / sequence / state diagram | **Mermaid** | `show_mermaid_diagram` or `open_page` page=`diagram viewer` — see `decisions-mermaid-diagrams` |
| Approve a multi-step implementation plan with inline diagrams | **visual-plan** | `/visual-plan` skill (BuilderIO) — MDX plan with diagram blocks |
| Review a branch/PR shape before line-by-line diff | **visual-recap** | `/visual-recap` skill (BuilderIO) |
| UI mockup, deck, branded prototype, hand-drawn whiteboard | **Open Design** | `decisions-open-design` — desktop app + `od mcp` |
| End agent turn with clear done/blocked signal | **quick-recap** | Green/yellow/red status block |

## Mermaid + visual-plan together

- **Architecture / data-flow / API sequence** in a plan → render with **`show_mermaid_diagram`** (Decisions freestanding viewer at `/diagram/`).
- **UI wireframes, artboards, prototype tabs** → use **visual-plan** top canvas (see vendored `visual-plan/references/canvas.md`).
- Do not duplicate: one Mermaid block for backend structure; canvas wireframes for screens — not both for the same fact.

## Reference clones

Sibling to DecisionsAI (same parent as `reference/ecc`):

- `../reference/builderio-skills` — [BuilderIO/skills](https://github.com/BuilderIO/skills)
- Vendored skills: `plugins/visual-plan-pack/skills/` (sync: `python3 scripts/sync_visual_plan_pack.py`)

## Agent-Native Plans app (optional)

Hosted or self-hosted Plan app for sharing visual-plan MDX outside the IDE. See BuilderIO [agent-native](https://github.com/BuilderIO/agent-native/) if you need shareable review links — Decisions Mermaid viewer covers the lightweight diagram case without that dependency.
