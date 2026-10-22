https://github.com/user-attachments/assets/cba7775e-f61e-4625-aefd-76417e72ca33

<h1 align="center">DecisionsAI</h1>

<p align="center"><strong>A local-first agent for your computer, projects, and phone.</strong></p>

<p align="center">
  <img src="https://img.shields.io/badge/macOS-14.0%2B-black?style=flat-square&logo=apple" alt="macOS" />
  <img src="https://img.shields.io/badge/Windows-10%2B-black?style=flat-square&logo=windows" alt="Windows" />
  <img src="https://img.shields.io/badge/Linux-black?style=flat-square&logo=linux" alt="Linux" />
  <img src="https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python" alt="Python 3.12" />
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="MIT License" /></a>
</p>

<p align="center">
  <a href="https://decisions.tensology.com/"><strong>Website</strong></a> ·
  <a href="#install"><strong>Install</strong></a> ·
  <a href="#how-it-fits-together"><strong>Architecture</strong></a> ·
  <a href="CHANGELOG.md"><strong>Changelog</strong></a>
</p>

## What it is

DecisionsAI is an open-source desktop agent that can talk, type, use tools, control your computer, and carry work through a real software project. It combines a voice-first desktop assistant with a local web workspace, durable development threads, workflows, connected services, and remote access from your phone.

The product is local-first. Speech, project context, memory, and models can stay on your machine. Cloud models and services are optional and only receive data when you configure and use them.

DecisionsAI is not tied to one model or coding tool. It can route work through local models, hosted providers, Codex, Cursor, Claude-compatible tools, Pi, and other configured CLIs while keeping the project, ticket, run history, and evidence together.

## Start with the Oracle

The Oracle is the small desktop avatar that stays above your windows. It is the quickest way to use DecisionsAI:

- Hold **Option + Command** on macOS to speak to the agent.
- Hold **Control + Command** to dictate into the app in front of you.
- Drop in a file, ask about the screen, run an action, or open Chat.
- Change shortcuts, audio devices, models, and voices in Preferences.

Voice can run through local speech recognition and text-to-speech, or through configured cloud providers. Push-to-talk, dictation, and continuous conversation are separate modes, so you can use only the parts you need.

## Give work a project

Quick questions stay in Chat. Substantial work belongs in **Development**, where it keeps a durable identity and the correct project context.

Development brings these surfaces together:

| Surface | Purpose |
|---|---|
| Planning | Shape the outcome and split larger work into sensible tasks |
| Incoming | Review requests from connected channels before they become work |
| Boards | Track tickets from backlog through QA and completion |
| Threads | Keep the conversation, tool activity, changes, and evidence together |
| Terminals | Follow coding CLI and harness sessions |
| Workflows | Run repeatable multi-step work with validation and retries |
| Reports | Review what changed, what passed, and what still needs a decision |

The agent chooses the lightest route that fits the request. A bounded edit can run directly in a Development thread. Repeatable or independently verifiable work can use a workflow. A completed run is still checked against the original request before it is treated as done.

## Use it away from your desk

Telegram can send text, voice notes, screenshots, documents, approvals, and corrections to the same agent. The secure remote interface adds screen viewing, clicking, scrolling, typing, and file transfer.

WhatsApp can feed selected contacts or groups into project intake. DecisionsAI can collect the relevant text and media, prepare a ticket, and wait for approval before work starts. Outbound replies remain drafts until you approve them.

## How it fits together

```text
Oracle, Chat, Telegram, WhatsApp, mobile remote
                         |
                  Decisions agent
                         |
       project + ticket + Development thread
                         |
       direct tools or a validated workflow run
                         |
        files, apps, models, CLIs, and services
                         |
             evidence, report, and memory
```

The main pieces are:

- `distr/core`: agent pipeline, tools, models, voice, memory, and workflows
- `distr/gui`: Oracle, tray, Preferences, and the local web interface
- `sidecar`: native computer control for keyboard, mouse, screen, and accessibility
- `plugins`: IDE adapters and vendored capability packs
- `skills`: focused instructions used by the agent and its harnesses
- `tests`: unit, integration, property, and browser-facing regression checks

MemPalace is the default consolidated memory backend. A bundled seed corpus supplies product conventions and planning context on first run. Legacy memory remains available as a fallback, and switching models does not require rebuilding project memory.

## Models and integrations

Different roles can use different workers: conversation, planning, coding, vision, image generation, Computer Use, workflow steps, and review. DecisionsAI supports local Ollama models and configured hosted providers such as OpenAI, Anthropic, OpenRouter, Groq, Google, ElevenLabs, Fish Audio, and AssemblyAI.

Connected services include Google Workspace, Telegram, WhatsApp, Discord, Jira, Trello, IDEs, and coding CLIs. Credentials are stored locally unless a service explicitly requires a relay or its own cloud API.

## Install

### One line

```bash
curl -fsSL https://decisions.tensology.com/install.sh | bash
```

### From source

```bash
git clone https://github.com/tensology/decisionsai.git
cd decisionsai
```

| Platform | Start command |
|---|---|
| macOS | Double-click `decisions.app`, or run `./bin/decisions.sh` |
| Windows | Run `bin/decisions.bat` or `bin/decisions.ps1` |
| Linux | Run `./decisions` |

The launcher checks dependencies, prepares Python, downloads the selected local models, and starts the app. Python 3.12, FFmpeg, and PortAudio are the main system requirements. Local models generally need at least 8 GB RAM; cloud-only use is lighter.

To prepare a source checkout manually inside an existing Python 3.12 environment:

```bash
python -m pip install -r requirements.txt
python bin/setup.py
python bin/start.py
```

## Development

Run the Python checks with the project's Python 3.12 environment:

```bash
python -m pytest
```

The setup and start paths also recalibrate installed Codex and Cursor integrations, project skills, and safe MCP configuration. More detail is available in [the orchestrator documentation](docs/orchestrator.md), [the sidecar README](sidecar/README.md), and the plugin READMEs under `plugins/`.

## Shared memory (MemPalace hub) and frustration capture

Decisions ships a local shared-memory stack, on by default on macOS:

- **MemPalace hub.** One `mempalace serve` process (pinned 3.11.0) on `127.0.0.1:8766`. It runs as the LaunchAgent `com.tensology.mempalace-hub` and owns the palace at `~/.decisions/mempalace/palace`. Requests need the bearer token stored 0600 under `~/.mempalace/server/`. Decisions reaches the hub over MCP/HTTP. If the hub is down, writes go to a local journal (`hub-journal.jsonl`) and are replayed later. Nothing listens beyond loopback.
- **Harness wiring.** Claude Code, Codex, Cursor and Kilo are wired only if they are installed. Each gets the same stdio server (`<venv>/python -m mempalace.mcp_server --palace …`), which forwards to the hub, so no token is written into harness configs. Pi gets the `mempalace-memory` skill. Every config is backed up as `<file>.bak-YYYYMMDD-mempalace` before the first change. Existing entries are preserved, and only the `mempalace` entry and marker blocks are removed on uninstall.
- **Frustration capture.** The LaunchAgent `com.tensology.decisions-selfimprove-capture` runs every 10 minutes and reads new harness transcript lines read-only. It classifies them with Laya, or with regex only when Laya isn't available, and redacts secrets and personal data. Each finding is filed as a `frustration.v1` event plus a drawer in wing `agent_frustrations`. Idle sessions get a wrap-up drawer in `agent_sessions`. Nothing hooks into a harness session.
- **Memory guard.** Each capture run is budgeted by `memguard`: capture plus its Laya worker may use at most 3072 MB physical footprint (`DECISIONS_CAPTURE_MAX_RSS_MB`), warn at 80%, stop cleanly at 90% and resume on the next run. Transcript lines are processed in batches of 25 (`DECISIONS_CAPTURE_BATCH_SIZE`).
- **Nightly review.** The LaunchAgent `com.tensology.decisions-selfimprove-nightly` runs at 02:00, every second night. It clusters recent frustrations and drops P1 and live-money projects. It then asks a reviewer for a small diff to the relevant `SKILL.md` or `AGENTS.md`. Reviewer order: `codex exec`, then `claude -p`, then Decisions LLM (OpenRouter models `minimax/minimax-m3.1-flash` (paid), then free `thinkingmachines/inkling-small:free`). If a reviewer fails, the next one is tried. Proposals are written to `~/.decisions/self-improve/proposals/<date>/` and are **never applied automatically**.

Commands (run from the repo root, using the Decisions venv):

```bash
python -m distr.core.mempalace.installer status
python -m distr.core.mempalace.installer install            # idempotent
python -m distr.core.mempalace.installer uninstall          # unload agents, remove harness entries
python -m distr.core.mempalace.selfimprove capture --dry-run
python -m distr.core.mempalace.selfimprove nightly --dry-run [--llm]
```

Off switches:

| Switch | Effect |
|---|---|
| Settings → `frustration_capture` off, or `DECISIONS_FRUSTRATION_CAPTURE=0` | no capture or nightly agents; both jobs exit at once |
| `DECISIONS_MEMPALACE_HUB=0` | no hub agent; Decisions uses MemPalace in-process |
| `DECISIONS_MEMPALACE_HARNESS_WIRING=0` | never touch harness configs |
| `DECISIONS_MEMPALACE_HARNESSES=claude,cursor` | wire only these harnesses |
| `DECISIONS_FRUSTRATION_LAYA=0` | regex-only detection |
| `DECISIONS_FRUSTRATION_REDACT=0` | turn redaction off (not recommended) |
| `DECISIONS_SELFIMPROVE_REVIEWER=codex\|claude\|decisions` | force one reviewer |
| `DECISIONS_SELFIMPROVE_LLM_PROVIDER` / `DECISIONS_SELFIMPROVE_LLM_MODELS` | change the Decisions-LLM reviewer |

All data stays on this machine. The only network call is the nightly reviewer, which sends a redacted prompt to the chosen reviewer.

## Development agent tools, MCP server mode and API (22 Oct 2026)

- **Development agent tools.** Development threads give Pi, Claude Code, Codex and Cursor the same toolset: image, vision, video (start/end frames), files (allowlisted folders, deletes go to the Trash), calendar (Google, or iCloud CalDAV), browser (dedicated `~/.decisions/chrome-profile`, off by default) and select/open thread. Risky actions wait for an Approve click in the thread. Computer use is still a disabled scaffold. The tools reach non-Pi harnesses through a per-run stdio MCP bridge (`DECISIONS_DEV_AGENT_TOOLS=1` is set only for those runs). Set `DECISIONS_CURSOR_DEV_MCP=off` to skip the temporary Cursor `.cursor/mcp.json`. Settings: Third party > Development agent tools.
- **MCP server mode.** Decisions can serve MCP at `/mcp/` (loopback only, bearer token, off by default). Use Settings > MCP > "MCP server (connect to Decisions)".
- **Routing.** Complexity routing has Low/Medium/High tiers, each with a fallback and an Auto flag. Automations can set their own harness, provider and model. Every harness dropdown uses `GET /api/harnesses/installed`.
- **Playwright** is pinned to `playwright==1.63.0`, and all installs share the browser cache `~/Library/Caches/ms-playwright`.

The HTTP API (auth, reply ids, settings, skills) is in `docs/api.md`. That file and the other `docs/` notes are local and not tracked in git.

## Privacy and safety

- No desktop telemetry is enabled by default.
- Local models, speech, memory, and project work can remain local.
- Cloud providers receive only the requests you send through them.
- Remote and messaging features use the Decisions relay and have separate retention rules.
- Tool mutations use shared safety controls so a stopped or repeated turn does not quietly repeat an external write.

Read the current [Privacy Policy](https://decisions.tensology.com/privacy) and [Terms and Conditions](https://decisions.tensology.com/terms) before connecting external accounts or sensitive data.

## Contributing

Issues and pull requests are welcome. Please check existing issues before opening a duplicate and keep changes focused enough to review and verify.

## License

DecisionsAI is licensed under the [MIT License](LICENSE.md). Third-party components and assets remain subject to their own notices and licences.

> DecisionsAI has no cryptocurrency or token. Any coin using the name is unaffiliated with this project.
