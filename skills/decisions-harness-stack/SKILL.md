---
name: decisions-harness-stack
description: Master index for DecisionsAI harness setup — ECC, Ponytail, Fallow, RTK, browser/content skills, and IDE thread tools.
---

# Decisions Harness Stack

One Decisions install enhances every CLI/IDE you already use. Run `bin/setup.py` once, then `bin/start.py` keeps projections current.

## Packs (projected to Codex, Cursor, Claude, Pi)

| Pack | Purpose |
|------|---------|
| **ECC** (`decisions-ecc-harness`) | Full vendored skill/agent/command surface under `plugins/ecc` |
| **Competition** (`decisions-competition-harness`) | Ponytail + Fallow skills and Cursor ponytail rule |
| **Capabilities** (`decisions-browser-content-harness`) | Browser QA, video/Remotion, content-engine, fal-ai-media |
| **Design references** (`decisions-design-reference-harness`) | Refero, Mobbin, Aceternity, Godly + UI ideation |
| **Agent Reach** (`decisions-agent-reach-harness`) | Internet research — Twitter, Reddit, YouTube, GitHub, web, RSS, Exa |
| **Community** (`decisions-community-skills-harness`) | humanizer, last30days, curated marketing + design aesthetics |
| **yt-dlp** (`decisions-yt-dlp-harness`) | YouTube metadata, subtitles, search — workflow `ytdlp` steps |
| **Composio** (`decisions-composio-harness`) | Composio Connect MCP (Tool Router) — Gmail, Slack, Notion, Jira, 1000+ apps |
| **Local** | decisions-playwright, decisions-browser-stack (this file) |

## CLIs

- **codex**, **cursor-agent**, **claude**, **pi** — worker skills in `plugins/*-ide/skills/decisions-*-worker`
- **rtk** — `scripts/setup_project_clis.sh` + hook init on setup/start
- **fallow** — `npx fallow` or global npm install from setup

## Decisions server tools

- `ide_thread` — list/read/prompt Codex and Cursor sessions
- `playwright_browser` — browser automation
- Workflow skill provision — pushes pre_chain skills into the active project harness

## State files

- `~/.decisions/harness/ecc-skills-registry.json`
- `~/.decisions/harness/capabilities-skills-registry.json`
- `~/.decisions/harness/mcp-recommendations.json` — catalog; **auto-merges** context7, Exa, Mobbin, Refero, Composio Connect into `~/.cursor/mcp.json` and `~/.codex/config.toml` (prunes deprecated Rube)

## Rules

- Prefer native Decisions skills when ids collide with ECC.
- Report completion through Decisions/Hermes when attached to a workflow; ambient events to `/api/harness/events` otherwise.
