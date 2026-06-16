---
name: decisions-browser-stack
description: Index of DecisionsAI browser automation — Playwright, browser-use, browser-qa, and workflow integration across Codex, Cursor, Claude, and Pi.
---

# Decisions Browser Stack

DecisionsAI projects browser skills into every installed harness on setup/start.

## Layers

| Layer | What it is |
|-------|------------|
| **Playwright** | Decisions venv + Chromium; Hermes tool + workflow steps |
| **browser-use** | Optional Python package (`pip install browser-use`) for agentic browsing |
| **ECC QA skills** | browser-qa, webapp-testing, e2e-testing |
| **RTK** | Shrinks noisy test/git output in agent hooks |

## MCP / API

- **fal.ai** — see `~/.decisions/harness/mcp-recommendations.json` for fal-ai MCP template (`FAL_KEY`)
- **Higgsfield** — not integrated in Decisions yet; use fal-ai-media or manual export

## Defaults in workflows

Workflow `pre_chain` merges ponytail, fallow (JS/TS projects), `decisions-harness-stack`, `browser-qa`, and `decisions-playwright`.

## Evidence

Always return screenshots, audit JSON, or URLs checked when closing a browser step.
