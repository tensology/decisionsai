---
name: decisions-mermaid-diagrams
description: Use Mermaid diagrams in DecisionsAI to explain technical topics — architecture, flows, sequences, and data models via the freestanding diagram viewer.
---

# Decisions Mermaid Diagrams

When explaining something **technical** — architecture, request flows, state machines, database relationships, deployment topology, or multi-step processes — prefer a **Mermaid diagram** over a long prose outline.

## When to diagram

Use a diagram when the user would benefit from seeing structure or flow:

- System or module architecture
- API / request sequences
- State transitions
- ER or data relationships
- CI/CD or workflow pipelines
- Before/after refactors

Skip diagrams for trivial one-line answers, pure opinion, or non-structural content.

## Related surfaces (not Mermaid)

| Need | Skill |
|------|-------|
| Rich implementation plan with wireframes + approval gate | `visual-plan` (BuilderIO) via `decisions-visual-plan` |
| PR/branch visual recap | `visual-recap` |
| UI prototype, deck, motion, hand-drawn whiteboard | `decisions-open-design` |

Use Mermaid for **text-native** technical graphs the agent can store in diagram History. Use Open Design for **pixel/CSS artifacts** and branded deliverables.

## How to show diagrams

### In DecisionsAI chat (server agent)

**Open the viewer only** (user says "open the mermaid viewer", no diagram yet):

- Call **`open_page`** with `page="diagram viewer"`, or
- Call **`show_mermaid_diagram`** with empty `mermaid_code` (reopens the last diagram from History, or a blank editor).

Do not ask what diagram to show — open the viewer immediately.

**Show a specific diagram:**

Call **`show_mermaid_diagram`** with:

- `mermaid_code` — valid Mermaid source (no markdown fences)
- `title` — short label for the viewer window (e.g. "Auth flow", "Module layout")

This opens a **freestanding viewer window** where the user can:

- See the rendered diagram
- Edit the Mermaid source and re-render
- Export **PNG** or **JPEG**
- Copy the diagram image or source code to the clipboard
- Browse **History** in the viewer sidebar (recent diagrams are saved locally)
- **Google Drawing** export when Google is connected (Settings → Advanced)

### In chat replies

You may still include a ` ```mermaid ` fenced block in markdown. Chat renders an **Open diagram** control that opens the same viewer.

### In Cursor (this IDE)

For standalone analytical artifacts in Cursor chat, a [canvas](file:///Users/paul/.cursor/skills-cursor/canvas/SKILL.md) may still be appropriate for data-heavy output. For classic Mermaid node/graph diagrams in DecisionsAI, use `show_mermaid_diagram` or fenced mermaid blocks.

## Mermaid quality bar

- One diagram = one idea. Split overloaded charts.
- Short node labels (2–4 words). Put detail in chat prose.
- Prefer `flowchart TD` / `sequenceDiagram` / `stateDiagram-v2` / `erDiagram` as appropriate.
- Left-to-right or top-down flow; avoid spaghetti crossing edges when possible.

## Example

```text
show_mermaid_diagram(
  title="Chat message path",
  mermaid_code='''sequenceDiagram
    participant U as User
    participant W as Web UI
    participant A as Agent
    U->>W: send message
    W->>A: POST /api/chats/{id}/messages
    A-->>W: stream tokens
    W-->>U: render reply'''
)
```

## Reference

- Viewer page: `/diagram/`
- API: `POST /api/diagrams` → `{ id, viewer_path }`
- Library: `/static/vendor/mermaid/mermaid.min.js` (Mermaid 11)
