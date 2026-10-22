---
name: mempalace-memory
description: Search or save to the user's shared local MemPalace memory (shared with Decisions, Claude Code, Codex, Cursor, Kilo). Use before answering about past work, decisions, people or projects, and to file durable decisions or learned facts.
---

# MemPalace memory (Pi)

Pi has no MCP client, so this skill talks to the local MemPalace hub through a small script.
Your identity is `mac:pi:<project>` where `<project>` is the current folder name in lowercase.

Search (do this before answering about past work; quote results, say so if nothing is found):

```bash
__PYTHON__ ~/.agents/skills/mempalace-memory/mempalace_memory.py search "what we decided about auth" --wing <project> --limit 5
```

Save a durable outcome (decision, conclusion, learned fact; never secrets or personal data):

```bash
__PYTHON__ ~/.agents/skills/mempalace-memory/mempalace_memory.py add --wing <project> --room decisions --text "Decided X because Y"
```

Check the hub: `__PYTHON__ ~/.agents/skills/mempalace-memory/mempalace_memory.py status`

Output is JSON. If the hub is down, `add` is queued locally and replayed later; `search` returns an error.
