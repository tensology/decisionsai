<p align="center">
  <img src="assets/readme/example.webp" alt="DecisionsAI" width="900" />
</p>

<h1 align="center">DecisionsAI</h1>

<p align="center">
  <strong>The last agent you'll ever need.</strong>
</p>

<p align="center">
  A voice-first, model-agnostic and CLI-agnostic orchestrator that turns requests into organized, validated work. Talk, dictate, type, or throw the job into Telegram. DecisionsAI remembers the project, chooses the right worker, runs the steps, asks when a real decision is needed, and reports the result back to you.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/macOS-14.0%2B-black?style=flat-square&logo=apple" alt="macOS" />
  <img src="https://img.shields.io/badge/Windows-10%2B-black?style=flat-square&logo=windows" alt="Windows" />
  <img src="https://img.shields.io/badge/Linux-black?style=flat-square&logo=linux" alt="Linux" />
  <img src="https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python" alt="Python 3.12" />
  <a href="LICENSE.md"><img src="https://img.shields.io/badge/license-Tensology-blue?style=flat-square" alt="License" /></a>
</p>

<p align="center">
  <a href="https://www.decisionsai.net/"><strong>Website</strong></a> ·
  <a href="#installation"><strong>Install</strong></a> ·
  <a href="#integrations"><strong>Integrations</strong></a> ·
  <a href="#voice-commands"><strong>Voice Commands</strong></a> ·
  <a href="#workflows"><strong>Workflows</strong></a> ·
  <a href="#ide-integration"><strong>IDE Harness</strong></a> ·
  <a href="docs/orchestrator.md"><strong>Orchestrator</strong></a>
</p>

---

## Your work has an operating system now

Chatbots answer. Coding CLIs edit. Local model runners serve tokens. DecisionsAI coordinates the whole job.

Give it a voice note, screenshot, ticket, email-shaped brief, Telegram message, or a linked WhatsApp conversation. It can preserve the source context, organize the request under the right project, create or update a ticket, choose a direct action or multi-step workflow, hand each step to the right model or CLI, validate the outcome, retain neutral memory, and keep you informed from your phone.

**Ollama runs models. DecisionsAI runs work.** Use Ollama underneath it, mix in Codex, Cursor, Claude Code, Pi, OpenRouter, or cloud APIs, and change any of them later without rebuilding your projects or losing their memory.

### What makes it different

| | Feature | Description |
|---|---|---|
| 🎙️ | **Voice first** | Push-to-talk conversations, fast system-wide dictation, voice notes, spoken progress, interruption, and offline Whisper/Kokoro options |
| 📱 | **Telegram work control** | Send text, voice notes, screenshots, files, or steering instructions; approve, reject, continue, stop, and receive progress or the final report |
| 🧠 | **Durable project memory** | Facts, decisions, changed files, evidence, blockers, and next actions survive model and CLI changes in neutral project memory |
| 🗂️ | **Projects + tickets** | Keep requests attached to real repositories, boards, source conversations, files, priorities, and acceptance criteria |
| 🔁 | **Step Runner + Loops** | Break work into scoped planning, implementation, review, browser QA, approval, and reporting steps with validation and correction loops |
| 🧭 | **Automatic orchestration** | In opt-in Auto mode, route by task role, complexity, risk, cost, capability, and prior failure evidence instead of using one model for everything |
| 🤖 | **Agent and CLI agnostic** | Codex, Cursor, Claude Code, Pi, local Ollama models, OpenRouter, OpenAI, Anthropic, Gemini, Groq, and other configured backends |
| 🧰 | **Skills + tools** | Project-scoped skills, MCP tools, browser automation, Computer Use, shell/Python, vision, Google Workspace, macros, screenshots, and evidence capture |
| 👀 | **Visible while autonomous** | Existing Runs and chat activity show provider/model, routing reason, current step, elapsed time, tools, skills, heartbeats, validation, and artifacts |
| 🔒 | **Private by default** | Local STT, LLM, TTS, memory, projects, and workflow records stay on disk unless you explicitly connect cloud services |
| 💬 | **WhatsApp-to-work bridge** | Link a feed to a Ticket Board, sync text/media, auto-snapshot configured conversations into tickets, and route approved tickets into work |
| 🎭 | **Animated skins** | Oracle plus extensible animated avatars with idle, thinking, working, and attention states |

<p align="center">
  <img src="assets/readme/avatar.webp" alt="DecisionsAI Skins" />
</p>

| | | |
|---|---|---|
| 📱 | **Control from your phone** | Link Telegram. Send work, voice or text, get replies and screenshots, stream the screen, transfer files |
| 📧 | **Google Workspace** | Gmail, Calendar, Drive, Docs, Sheets over direct API access |
| 🎙️ | **Voice cloning** | Clone voices from audio clips with Kokoro (offline) or ElevenLabs |
| 🔄 | **Recorded macros** | Record keyboard and mouse sequences, replay them by voice |
| 👁️ | **Vision** | Send screenshots, photos, or diagrams; the assistant uses your vision model on them |
| 🔀 | **Workflows + Loops** | Multi-step workflows with loop presets, Step Runner execution, validation, harness steering, recording, browser evidence, and scheduling |
| 📅 | **Automations + calendar** | Itemized automations with scheduling, time-entry blocks, live timers, and timesheet export |
| 🖥️ | **Screen intelligence** | Vision-based screen analysis, pixel-precise element location via Computer Use API, accessibility tree walking |
| 🐍 | **Python executor** | The agent can write and run Python for file ops, image processing, scraping, and other scripted tasks |
| 🧭 | **[Orchestrator](docs/orchestrator.md)** | Integrated orchestration ledger for chat, ticket boards, workflows, automations, browser evidence, IDE handoffs, planning, and long-running work memory |
| 🔧 | **[IDE + coding agents](#ide-integration)** | Unified **harness stack** for [Codex](plugins/codex-ide/README.md), [Cursor](plugins/cursor-ide/README.md), [Claude-compatible harnessing](plugins/ecc/docs/HERMES-SETUP.md), and Pi — skills, MCP merge, Agent Reach, design references, Composio Connect, yt-dlp workflow steps, and IDE thread tools |
| 📺 | **Terminal overview** | The assistant glances at your terminal tab and reacts to build errors, test failures, or anything on screen |
| 🌐 | **Remote control** | HMAC-encrypted browser UI with Snippets, Agent, and Dictate. Hold to talk or tap for a text box |

## From request to verified result

```
Voice · Dictation · Telegram · WhatsApp · Web · Ticket Board
                              │
                              ▼
             Project context + durable ticket + memory
                              │
                              ▼
       Orchestrator: direct action or scoped workflow/loop?
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    Plan / research     Build / operate      Review / validate
    Codex · local       Ornith · Cursor       independent model
    OpenRouter · ...    Claude Code · Pi      browser · tests · tools
          └───────────────────┼───────────────────┘
                              ▼
               Evidence + memory + report + approval
                              │
                              ▼
                    Telegram · Chat · Runs
```

1. **Choose the brains.** Configure the conversational/orchestrator LLM, coding LLM, vision model, Computer Use model, Step Runner, and preferred CLIs independently—or enable Auto routing.
2. **Connect the work surfaces.** Telegram is the remote control. Projects, Ticket Boards, WhatsApp links, Gmail, Jira, Trello, and the web UI provide durable context.
3. **Ask naturally.** Dictate on the desktop or send a Telegram text, voice note, image, or file. The orchestrator decides whether to answer, act, create a ticket, ask a question, request approval, or run a workflow.
4. **Let specialists work.** A planner can scope with Codex, an implementation step can run with local Ornith, and a separate model can review—without sharing model-specific memory.
5. **See proof, not a spinner.** Follow the current step, selected provider/model, rationale, elapsed time, heartbeat, evidence, validation, retry, and final report in existing Runs or chat.

---

## The Local Web Interface

DecisionsAI spins up a **local-only** web UI (not exposed to the internet). Open it from the Oracle menu or the in-app tray.

| Section | What you do there |
|---|---|
| **Preferences** | Choose models and voices, add API keys, connect Google and Telegram, tune behavior |
| **Skins** | Browse and swap avatar skins in Preferences |
| **Chat** | Switch model and voice inside a thread, compact context, fork chats, and read system activity inline |
| **Actions** | View, edit, rename, and trigger recorded macros |
| **Snippets** | Manage text or code snippets with trigger words |
| **Projects** | Project workspace with context blocks, linked files, and IDE/coding backend setup |
| **Ticket Boards** | Manage local, Jira, and Trello work; link WhatsApp numbers to a board; send tickets straight into the orchestrator |
| **Automations** | Scheduled instruction workflows with Run Now, history, and a calendar for time-entry blocks linked to tickets |
| **Workflows** | Multi-step workflows with **Loops** presets, Step Runner execution, validation, harness steering, browser evidence, and scheduling |
| **IRC** | Built-in IRC chat page for shared rooms alongside Telegram and WhatsApp |
| **Skills** | Browse local and vendored skills, including [ECC-backed capabilities](plugins/ecc/README.md), without duplicate setup |

<p align="center">
  <img src="assets/readme/chat.webp" alt="DecisionsAI Web Interface" />
</p>

---

## Technology Stack

**Offline core:**

| Component | Role |
|---|---|
| [Whisper.cpp](https://github.com/ggerganov/whisper.cpp) | Fast, accurate offline speech recognition |
| [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) | High-quality offline TTS + custom voice cloning (on-device) |
| [Coqui TTS](https://github.com/coqui-ai/TTS) | Multi-speaker offline TTS (VCTK, 100+ speakers) |
| [Ollama](https://ollama.ai/) | Local LLM inference (Llama, Gemma, Qwen, and more) |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | Real-time voice pipeline orchestration |
| **[Orchestrator](docs/orchestrator.md)** | Internal orchestration ledger for ticket routing, IDE sessions, browser evidence, validation, correction loops, and run memory |
| **[Sidecar (Go)](sidecar/README.md)** | Machine control: accessibility tree, mouse/keyboard, screenshots, drag, scroll, Python execution |

**Optional cloud services:**

| Service | What it adds |
|---|---|
| [OpenRouter](https://openrouter.ai/) | Unified access to GPT-5.4, Gemini 3 Flash, Claude, and every new model as it drops |
| [OpenAI](https://openai.com/) | GPT-5.4, GPT-4 Turbo, GPT-4o |
| [Anthropic](https://www.anthropic.com/) | Claude 3.7 Sonnet, Claude 3.5 Opus, Claude 3 Haiku |
| [ElevenLabs](https://elevenlabs.io/) | Cloud TTS with voice cloning (up to 5 custom voices) |
| [AssemblyAI](https://www.assemblyai.com/) | Advanced transcription and speech recognition |

---

## Legal References

Public policy links:

| Document | URL | Policy check date |
|---|---|---|
| Privacy Policy | <https://www.decisionsai.net/privacy> | 2026-05-22 |
| Terms and Conditions | <https://www.decisionsai.net/terms> | 2026-05-22 |

The public pages should show their own last-updated dates. During the
2026-05-22 check, no visible last-updated date was found in the fetched page
content.

The legal pages should explicitly cover connected accounts and external streams
such as WhatsApp, Telegram, Gmail, Jira, Trello, IRC/shared chat rooms, uploaded
files, voice notes/transcriptions, images, project folders, CLI/IDE execution
logs, model-provider requests, workflow audit trails, and the internal
[orchestration ledger](docs/orchestrator.md) used for validation and correction memory.

---

## System Requirements

### Offline / local mode

| | |
|---|---|
| **OS** | macOS (Apple Silicon & Intel), Windows, Linux |
| **RAM** | 8 GB minimum; 12 GB recommended for ornith:9b |
| **Python** | 3.12 |
| **System deps** | PortAudio, FFmpeg |
| **Disk** | ~200 MB for cloud models; ~6 GB for full local models |

> DecisionsAI detects your system RAM at first launch and picks models that fit. Cloud models (marked `:cloud`) run on Ollama's servers with zero local RAM for the weights.
>
> **Recommended (cloud, any Mac):**
>
> | Role | Model | RAM needed |
> |---|---|---|
> | Chat | `ornith:9b` | ~6 GB |
> | Coding | `ornith:9b` | ~6 GB |
> | Vision | `qwen3-vl:2b` | ~1.9 GB |
> | Image | `x/flux2-klein:latest` | local only |
>
> **Local-only fallbacks (10 GB+ RAM):**
>
> | RAM | Chat model | Coding model | Approx. VRAM |
> |---|---|---|---|
> | 10–11 GB | `qwen3:4b` | `qwen2.5-coder:3b` | ~3.5 GB |
> | 12+ GB | `ornith:9b` | `ornith:9b` | ~6 GB |

### Online / cloud mode

| | |
|---|---|
| **RAM** | 4 GB minimum, 8 GB recommended |
| **Disk** | ~200 MB |
| **Internet** | Stable connection required |

No large model downloads. Only Whisper.cpp and Kokoro install locally. Use local models for sensitive work and cloud models when you need bigger weights.

<p align="center">
  <img src="assets/readme/about.webp" alt="DecisionsAI About" />
</p>

---

## Installation

### One-liner

```bash
curl -fsSL https://decisionsai.net/install.sh | bash
```

### Quick start

```bash
git clone https://github.com/tensology/decisionsai.git
cd decisionsai
```

| Platform | Command |
|---|---|
| **macOS** | Double-click `decisions.app`, or `./bin/decisions.sh` |
| **Windows** | Double-click `bin/decisions.bat`, or `bin/decisions.ps1` |
| **Linux** | `./decisions` |

The launcher handles dependency checks, Python setup, model downloads, and launch automatically.

When [Codex](plugins/codex-ide/README.md), [Cursor](plugins/cursor-ide/README.md), or the [Claude-compatible harness surface](plugins/ecc/docs/HERMES-SETUP.md) are available on the machine, setup and every `bin/start.py` run recalibrate the **harness stack**: repair IDE plugins, re-project skills (so plugin reinstall does not wipe them), refresh MCP recommendations, and merge safe MCP servers into Cursor/Codex configs. Third-party keys such as Composio and Cursor API tokens are stored in **Preferences → API Keys** and injected at recalibrate time — you should not need to edit `~/.cursor/mcp.json` by hand.

### Manual installation

```bash
# 1. Python environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Dependencies
pip install -r requirements.txt

# 3. Download AI models
python bin/setup.py

# 4. Start
python bin/start.py
```

**System deps:** `brew install portaudio ffmpeg` (macOS) · `sudo apt-get install portaudio19-dev ffmpeg` (Linux) · winget/Chocolatey/Scoop (Windows)

### Optional components

| Component | Install | Notes |
|---|---|---|
| **Vosk** (alt STT) | `python bin/setup_vosk.py` | ~1.8 GB English model |
| **Voice cloning** | Built-in for Kokoro and ElevenLabs | Click **+ Custom** next to voice dropdown in Preferences |

---

## Keyboard & Voice Commands

### Voice commands

Exact phrasing can vary.

| Category | Examples |
|---|---|
| **Navigation** | "Open Safari", "Focus on Slack", "New tab", "Close", "Open spotlight" |
| **Text editing** | "Copy", "Paste", "Undo", "Select all", "Delete line" |
| **Mouse** | "Mouse up", "Click", "Double click", "Scroll down", "Move mouse center" |
| **AI assistant** | "Dictate", "Transcribe", "Explain this", "Rework this", "Summarize this", "Translate" |
| **Macros** | "Start recording", "Stop recording", "Run action [name]" |
| **Media** | "Pause", "Next track", "Volume up", "Mute" |
| **System** | "Start listening", "Stop speaking", "Exit" |

### Global shortcuts

Defaults are editable in **Preferences → Shortcut Keys**.

| Shortcut | Action |
|---|---|
| Hold `Option + Command` | Push-to-talk |
| Hold `Control + Command` | Hold-to-dictate |
| `Cmd + Option + C` | Open Chat web UI |
| `Cmd + Option + J` | Open Projects web UI |
| `Cmd + Option + A` | Open Actions web UI |
| `Cmd + Option + N` | Open Snippets web UI |
| `Cmd + Option + W` | Open Workflows web UI |
| `Cmd + Option + ~` | Open Preferences web UI |
| `Control + Command + Left / Right` | Previous / next skin |
| `Cmd + Option + 1..9` | Select skin by index (`1` = Oracle) |
| `Control + Command + Up / Down` | Increase / decrease Oracle size |
| `Cmd + Option + S` | Toggle recording start/stop |

---

## Integrations

### Telegram

Connect in **Preferences → Advanced → Telegram**. Telegram is the fastest way to hand DecisionsAI work from anywhere: send text, a voice note, screenshot, document, correction, or follow-up. The same orchestrator can answer directly, create/update a project ticket, start a workflow, pause for a decision, or steer an existing run. Inline buttons and typed or spoken replies support approve, reject, continue, stop, and feedback. Progress and final reports return to the same conversation. Type `remote` for an HMAC-encrypted link to the full remote-control UI: screen stream, click, type, scroll, and file transfer.

### WhatsApp

Connect WhatsApp in **Preferences → Advanced**, then link a contact or group feed to a Ticket Board. DecisionsAI can sync incoming text, voice/media metadata, captions, and conversation context; configured boards can auto-snapshot new messages into durable tickets. From there the normal approval and orchestration policy can route the ticket to a project, direct action, or workflow. This deliberately keeps ordinary conversation from becoming unintended execution: only the feeds and autonomy boundaries you configure become work.

### Google Workspace

Connect via **Preferences → Advanced → Google** (OAuth 2.0).

| Service | Capabilities |
|---|---|
| **Gmail** | Read, send, draft, reply |
| **Calendar** | Create events, check schedule |
| **Drive** | List, read, upload |
| **Docs** | Create from Markdown |
| **Sheets** | Read and interact |

### IDE Integration

[Codex](plugins/codex-ide/README.md), [Cursor](plugins/cursor-ide/README.md), the [Claude-compatible harness surface](plugins/ecc/docs/HERMES-SETUP.md), and other coding backends attach to project context. Ticket-board work, IDE chats, workflow runs, and automation runs can report back to the orchestrator when the project link is set.

Setup installs or repairs the local [Codex](plugins/codex-ide/README.md) and [Cursor](plugins/cursor-ide/README.md) plugins when those tools are present. They emit session events into [Orchestrator](docs/orchestrator.md) so the desktop agent can see IDE progress instead of losing it inside the editor.

**Harness stack** (orchestrated from `distr/core/harness_stack.py`, run on `bin/setup.py` and quietly on every `bin/start.py`):

| Pack | What it adds |
|---|---|
| ECC | Vendored skills, agents, commands (`plugins/ecc`) |
| Competition | Ponytail + Fallow skills and Cursor ponytail rule — see [ponytail/fallow assessment](audit-docs/ponytail-fallow-reference-assessment.md) |
| Capabilities | Browser QA, Playwright, content-engine, fal-ai-media |
| Design references | Refero, Mobbin, Aceternity, Godly + UI ideation skills |
| Agent Reach | Public web/social research (Twitter, Reddit, YouTube, Exa, …) |
| Community skills | humanizer, last30days, curated marketing + design aesthetics |
| yt-dlp | YouTube metadata/subtitles + workflow `ytdlp` steps |
| Composio Connect | SaaS tool Router MCP (replaces deprecated Rube) |
| MCP harness | Catalog + add-only merge into Cursor/Codex; see `~/.decisions/harness/mcp-recommendations.json` |

Workflow runs can push a **pre_chain** of skills into the active project harness (browser, design, agent-reach, composio, yt-dlp, etc.) based on ticket text and project surface.

The Decisions agent exposes **`ide_thread`** to list, read, and prompt Codex/Cursor sessions. **Composio** API keys live under **Preferences → API Keys**; saving recalibrates MCP headers automatically.

[Orchestrator](docs/orchestrator.md) holds tickets, boards, automations, workflows, browser runs, project folders, and IDE sessions in one ledger. Local skills live under `skills/`; vendor packs under `plugins/*-pack/`.

### Models are roles, not a lock-in

Preferences separates the conversational/orchestrator LLM, coding LLM, vision model, image model, Computer Use model, Step Runner, and board agent. Each workflow step can override the backend, provider, model, tools, skills, cost tier, and independence requirement.

With opt-in **Auto** routing, DecisionsAI evaluates the step role, complexity, risk, capability requirements, and recorded failures. It can plan with Codex, implement bounded work with a free/local Ornith model, choose an independent reviewer such as HY3, and escalate to more expensive providers only when the lower tier has produced evidence that it is stuck. The chosen route and reason appear immediately in Runs/chat activity.

[Ornith 1.0](https://huggingface.co/deepreinforce-ai/Ornith-1.0-35B) is a particularly strong local fit: the open-source agentic-coding family includes 9B and 35B variants with tool calling. DecisionsAI supports `ornith:9b` for accessible local work and configured 35B variants for more capable machines, while keeping the project memory portable if you swap it out later.

---

## Workflows

Multi-step workflows run on the **Step Runner**. **Loops** are reusable development and operational playbooks on top of that runner. A group of project tickets can move through planning, implementation, independent review, tests, browser QA, approval, and reporting while each step uses a different model or CLI. Import a loop preset, steer a waiting harness from Telegram or the web UI, and inspect validation, routing history, executor context, evidence, heartbeats, and reports in the existing Runs/chat surfaces.

| Concept | How it works |
|---|---|
| **Actions** | Agent instructions, recorded macros, shell commands, HTTP requests, Playwright scripts, or **yt-dlp** (metadata/subtitles/search) |
| **Tool-calling agent** | Each step uses an LLM with native tool calling |
| **Validation** | Text matching, rule-based checks, LLM judgment, or screenshot comparison |
| **Static routing** | Pick a "go to" step for pass/fail |
| **Agent routing** | Prompt the agent; it picks the next step |
| **Recording** | 3-2-1 countdown, captures keyboard + mouse, replays automatically |
| **Presets** | Export/import `.dwf` bundles (workflow + recordings + screenshots) |
| **Scheduling** | Hourly, daily, or weekly on specific days |
| **Agent Context** | One context block for rules, credentials, and conventions prepended to step prompts |
| **Per-step workers** | Choose or auto-detect provider, model, CLI, capabilities, skills, tools, cost tier, and reviewer independence |
| **Neutral memory** | Save facts, decisions, evidence, files changed, blockers, and next actions without binding the project to one harness |
| **Remote interaction** | Approve, reject, continue, stop, or steer from Telegram; mirror meaningful progress and results back to the originating channel |

<p align="center">
  <img src="assets/readme/steprunner.webp" alt="Workflows" />
</p>

---

## Project Structure

```
bin/                 # Launchers and setup scripts
distr/
├── core/            # Agent pipeline, LLM, STT, TTS, actions, workflows
├── gui/
│   ├── dialogs/     # About, preferences windows
│   ├── oracle/      # Oracle overlay and tray
│   └── web/         # Local web UI (templates, static, API)
plugins/             # IDE plugins (codex-ide, cursor-ide), ECC, and vendor harness packs (competition, agent-reach, community-skills, yt-dlp)
skills/              # Local Decisions skills (harness stack, playwright, composio, design references, …)
sidecar/             # Go binary: machine control (macOS/Windows)
assets/
├── avatars/         # Skin packs (Clippy, Nugget, Rusty, Masko, etc.)
└── readme/          # README images
docs/                # Orchestrator docs (local planning notes stay gitignored)
.artifacts/          # Gitignored local runtime output (tickets, pi skills, cursor handoffs)
tests/               # Property-based and unit tests
```

---

## Contributing

Suggestions, bug reports, and pull requests are welcome. Open an issue to discuss a change before sending a PR. Check existing issues first to avoid overlap.

## License

Licensed under the TENSOLOGY COMMUNITY LICENSE AGREEMENT. See [LICENSE.md](LICENSE.md) for details.

---

> **Note:** This project has no cryptocurrency or token associated with it. Any coin using the DecisionsAI name is not affiliated with us.
