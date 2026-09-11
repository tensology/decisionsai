---
name: decisions-design-references
description: Playbook for Aceternity UI, Refero MCP, Mobbin MCP, and Godly when building or polishing web UI — what each source is for and how agents should use it.
---

# Design Reference Sources

DecisionsAI does not replace these products — it tells agents **which tool for which job** and how to wire MCPs.

## Quick pick

| Need | Use |
|------|-----|
| Paste-ready React/Tailwind/Motion components | **Aceternity UI** — ui.aceternity.com |
| Real shipped screens + DESIGN.md tokens | **Refero MCP** — refero.design/mcp (Pro) |
| Pattern library + flows (mobile/web) | **Mobbin MCP** — api.mobbin.com/mcp (Pro, OAuth) |
| Visual inspiration only | **Godly** — godly.website (manual screenshots/links) |
| ECC implementation taste | `frontend-design`, `frontend-design-direction` |

**“10x”** — if someone lists five sites and the fifth is “10x”, it is usually slang or a misheard name, not a fifth MCP. Ask the user for the exact URL unless they confirm a product.

---

## Aceternity UI (no MCP)

- Site: https://ui.aceternity.com
- Stack: React, Tailwind CSS, Framer Motion
- **Workflow:** open the component page → copy the published code → adapt to project structure (paths, imports, design tokens).
- **Agent tip:** put the component URL in the prompt: “Implement the bento grid from ui.aceternity.com/components/…”
- Do not invent Aceternity APIs — only use what’s on the page.

---

## Refero (official MCP + optional skill)

- MCP docs: https://refero.design/mcp
- Library: styles.refero.design (DESIGN.md extracts)
- **Requires:** Refero Pro; browser OAuth on first MCP call.
- **MCP gives:** search over 130k+ screens and flows with structured metadata (patterns, layouts).
- **Optional skill:** `npx skills add https://github.com/referodesign/refero_skill`
- **Example prompts:**
  - “Find onboarding flows from fintech apps”
  - “Show how Linear handles empty states”
  - “Extract typography and spacing for a dense settings page”
- Paste extracted tokens into the repo as CSS variables or tailwind theme extension.

---

## Mobbin (official HTTP MCP)

- Site: https://mobbin.com
- Endpoint: `https://api.mobbin.com/mcp`
- **Requires:** Mobbin Pro (~€10/month); OAuth via browser on first use.
- **Claude Code setup:**
  ```bash
  claude mcp add mobbin --scope user --transport http https://api.mobbin.com/mcp
  ```
- **Cursor:** add to `~/.cursor/mcp.json`:
  ```json
  {
    "mcpServers": {
      "mobbin": {
        "url": "https://api.mobbin.com/mcp"
      }
    }
  }
  ```
- Use for: navigation patterns, paywalls, onboarding, mobile-specific layouts.
- Cite which pattern you copied and why it fits the ticket.

---

## Godly (gallery only)

- Site: https://godly.website
- No API, no MCP.
- **Workflow:** user or agent picks 1–3 gallery pages → attach screenshots or URLs → describe what to borrow (typography mood, color, density).
- Do not claim Godly integration — treat as human-curated mood board.

---

## Decisions integration

- **Ideation first:** `decisions-ui-ideation`
- **Setup:** `~/.decisions/harness/mcp-setup-design.sh` and `mcp-recommendations.json`
- **Workflows:** UI steps should list this skill + `frontend-design`
- **Evidence:** in result packets, list references used (Refero query, Mobbin pattern, Aceternity URL, Godly link).

---

## When MCP is unavailable

1. Use repo existing UI as primary reference.
2. Aceternity component URLs for concrete components.
3. Godly links/screenshots for direction.
4. State in `Blockers:` or `Summary:` that Refero/Mobbin need Pro + MCP setup — do not fake live search results.
