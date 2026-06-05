<p align="center">
  <img src="assets/readme/example.webp" alt="DecisionsAI" width="900" />
</p>

<h1 align="center">DecisionsAI</h1>

<p align="center">
  A voice-first desktop assistant and local orchestration harness that listens, reasons, speaks back, and acts — opening apps, editing text, running macros, managing workflows, and keeping project work connected — all on your machine, with your choice of AI.
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
  <a href="docs/hermes.md"><strong>Hermes</strong></a>
</p>

---

## Features

| | Feature | Description |
|---|---|---|
| 🔒 | **Private by default** | Everything runs locally — offline STT (Whisper.cpp), local LLM (Ollama), offline TTS (Kokoro). Nothing leaves your machine unless you add API keys |
| 🤖 | **Every major AI provider** | OpenAI, Anthropic, ElevenLabs, OpenRouter — swap models anytime, never locked in |
| 🎭 | **Animated skins** | Clippy, Nugget, Rusty, Masko, Madame Patate — each with idle, thinking, working, and attention animations. Drop a folder with `skin.json` for your own |

<p align="center">
  <img src="assets/readme/avatar.webp" alt="DecisionsAI Skins" />
</p>

| | | |
|---|---|---|
| 📱 | **Control from your phone** | Connect Telegram — send voice or text, get replies and screenshots back, stream your screen live, transfer files |
| 📧 | **Google Workspace** | Gmail, Calendar, Drive, Docs, Sheets — direct API access, no third-party routing |
| 🎙️ | **Voice cloning** | Clone voices from audio clips with Kokoro (offline) or ElevenLabs |
| 🔄 | **Recorded macros** | Capture keyboard and mouse sequences, replay them by voice — perfect for repetitive tasks |
| 👁️ | **Vision** | Share screenshots, photos, or diagrams — the assistant reasons about what it sees |
| 🔀 | **Workflows + automations** | Multi-step workflows with validation, agent routing, recording, presets, scheduling, and itemized automations |
| 🖥️ | **Screen intelligence** | Vision-based screen analysis, pixel-precise element location via Computer Use API, accessibility tree walking |
| 🐍 | **Python executor** | The agent writes and runs Python scripts for complex tasks — file ops, image processing, web scraping, anything |
| 🧭 | **[Hermes orchestration](docs/hermes.md)** | Integrated orchestration ledger for chat, ticket boards, workflows, automations, browser evidence, IDE handoffs, planning, and long-running work memory |
| 🔧 | **[IDE + coding agents](#ide-integration)** | Work with [Codex](codex_plugin/decisions-codex/README.md), [Cursor](cursor_plugin/decisions-cursor/README.md), [Claude-compatible harnessing](vendor/ecc/docs/HERMES-SETUP.md), and coding backends through local plugins and harness reporting so project context does not disappear |
| 📺 | **Terminal overview** | The assistant glances at your terminal tab and reacts to build errors, test failures, or anything on screen |
| 🌐 | **Remote control** | HMAC-encrypted browser UI — click, type, scroll, drag, and transfer files from anywhere |

## How It Works

```
┌──────────────┐    voice / text     ┌──────────────┐     actions      ┌──────────────┐
│     You      │ ──────────────────▶ │  DecisionsAI │ ──────────────▶  │   Your Mac   │
│  (mic/chat)  │ ◀────────────────── │  (Pipecat)   │                  │   / PC       │
└──────────────┘    speech / reply   └──────────────┘                  └──────────────┘
                                            │
                                     ┌──────┴──────┐
                                     │  Local LLM  │
                                     │  or Cloud   │
                                     └─────────────┘
```

1. **Install and launch** — the bundled launcher handles dependencies, Python, model downloads, and startup.
2. **Pick your AI** — local (Ollama) for privacy, cloud (OpenAI / Anthropic / OpenRouter) for power, or mix both.
3. **Talk naturally** — speak a command, the assistant reasons and acts. Interrupt mid-sentence if you want.
4. **Choose a skin** — pick an animated avatar or stick with the classic Oracle orb.

---

## The Local Web Interface

DecisionsAI spins up a **local-only** web UI (not exposed to the internet). Open it from the Oracle menu or the in-app tray.

| Section | What you do there |
|---|---|
| **Preferences** | Choose models and voices, add API keys, connect Google and Telegram, tune behavior |
| **Skins** | Browse and swap avatar skins in Preferences |
| **Chat** | Browse and manage conversation threads |
| **Actions** | View, edit, rename, and trigger recorded macros |
| **Snippets** | Manage text or code snippets with trigger words |
| **Projects** | Create projects with context blocks, linked files, and IDE/coding backend setup |
| **Ticket Boards** | Manage local, Jira, and Trello work, then send tickets into the orchestrator with linked project context |
| **Automations** | Create scheduled instruction workflows with Run Now and per-automation history |
| **Workflows** | Build multi-step workflows with validation, routing, recording, browser evidence, presets, and scheduling |
| **Skills** | Browse local and vendored skills, including [ECC-backed capabilities](vendor/ecc/README.md), without duplicate setup |

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
| [Coqui TTS](https://github.com/coqui-ai/TTS) | Multi-speaker offline TTS (VCTK voices — 100+ speakers with accents) |
| [Ollama](https://ollama.ai/) | Local LLM inference (Llama, Gemma, Qwen, and more) |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | Real-time voice pipeline orchestration |
| **[Hermes](docs/hermes.md)** | Internal orchestration ledger for ticket routing, IDE sessions, browser evidence, validation, correction loops, and run memory |
| **[Sidecar (Go)](sidecar/README.md)** | Machine control — accessibility tree, mouse/keyboard, screenshots, drag, scroll, Python execution |

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
[orchestration ledger](docs/hermes.md) used for validation and correction memory.

---

## System Requirements

### Offline / local mode

| | |
|---|---|
| **OS** | macOS (Apple Silicon & Intel), Windows, Linux |
| **RAM** | 8 GB minimum; 12 GB recommended for qwen3:8b |
| **Python** | 3.12 |
| **System deps** | PortAudio, FFmpeg |
| **Disk** | ~200 MB for cloud models; ~6 GB for full local models |

> DecisionsAI detects your system RAM at first launch and picks models that fit. Cloud models (marked `:cloud`) run on Ollama's servers — zero local RAM needed.
>
> **Recommended (cloud — any Mac):**
>
> | Role | Model | RAM needed |
> |---|---|---|
> | Chat | `minimax-m2.5:cloud` | 0 (cloud) |
> | Coding | `glm-5.1:cloud` | 0 (cloud) |
> | Vision | `qwen3-vl:2b` | ~1.9 GB |
> | Image | `x/flux2-klein:latest` | local only |
>
> **Local-only fallbacks (10 GB+ RAM):**
>
> | RAM | Chat model | Coding model | Approx. VRAM |
> |---|---|---|---|
> | 10–11 GB | `qwen3:4b` | `qwen2.5-coder:3b` | ~3.5 GB |
> | 12+ GB | `qwen3:8b` | `qwen2.5-coder:7b` | ~6 GB |

### Online / cloud mode

| | |
|---|---|
| **RAM** | 4 GB minimum, 8 GB recommended |
| **Disk** | ~200 MB |
| **Internet** | Stable connection required |

No large model downloads. Only Whisper.cpp and Kokoro install locally. Mix and match — use local models for sensitive tasks, cloud models for heavy reasoning.

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

When [Codex](codex_plugin/decisions-codex/README.md), [Cursor](cursor_plugin/decisions-cursor/README.md), or the [Claude-compatible harness surface](vendor/ecc/docs/HERMES-SETUP.md) are available on the machine, setup also verifies the DecisionsAI harness wiring for them. Codex and Cursor receive local DecisionsAI plugin/reporting files so IDE conversations and project work can speak back to [Hermes](docs/hermes.md); Claude receives the compatible [ECC harness surface](vendor/ecc/docs/HERMES-SETUP.md). If an IDE is not installed or DecisionsAI is not running, those checks fail quietly instead of blocking launch.

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

Speak naturally — exact phrasing can vary.

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

Connect in **Preferences → Advanced → Telegram**. Send voice or text to your bot and get replies, voice notes, and screenshots back. Type "remote" for an HMAC-encrypted link to the full remote control UI — stream your screen, click, type, scroll, and transfer files from anywhere.

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

[Codex](codex_plugin/decisions-codex/README.md), [Cursor](cursor_plugin/decisions-cursor/README.md), the [Claude-compatible harness surface](vendor/ecc/docs/HERMES-SETUP.md), and other coding backends are wired through project context so ticket-board work, free-form IDE chats, workflow runs, automation runs, and orchestrator updates can stay attached to the right project where possible. The CLI path still exists, but DecisionsAI now treats the IDEs as first-class development surfaces.

The local [Codex](codex_plugin/decisions-codex/README.md) and [Cursor](cursor_plugin/decisions-cursor/README.md) plugins are installed or repaired during setup when those tools are detected. They report project/session events back into [Hermes](docs/hermes.md), which lets the main orchestrator continue a thread, see whether a project is moving, attach browser or workflow evidence, and speak to you from the right channel instead of losing the work inside a separate editor chat.

[Hermes](docs/hermes.md) acts as the shared harness for that loop: tickets, boards, automations, workflows, browser runs, local project folders, and IDE conversations all feed the same project-aware ledger. The vendored [ECC](vendor/ecc/README.md) surface adds skills, harness patterns, and setup references without blindly duplicating local skills. Together, that is what lets DecisionsAI answer questions like "what is my daily plan?", "what is stuck?", or "what happened in Codex on this project?" using more than the last chat message.

---

## Workflows

Build multi-step workflows the agent executes in sequence. Each step has an action, optional validation, and routing logic. The workflow agent has full tool access — it can take screenshots, click elements, run Python scripts, search the web, use browser evidence, and hand project work to IDE/coding agents through the orchestrator.

| Concept | How it works |
|---|---|
| **Actions** | Agent instructions, recorded macros, shell commands, HTTP requests, or Playwright scripts |
| **Tool-calling agent** | Each step runs through a dedicated LLM with native tool calling — it actually does things instead of describing what it would do |
| **Validation** | Text matching, rule-based checks, LLM judgment, or screenshot comparison |
| **Static routing** | Pick a "go to" step for pass/fail |
| **Agent routing** | Give the agent a prompt — it picks the next step dynamically |
| **Recording** | 3-2-1 countdown, captures keyboard + mouse, replays automatically |
| **Presets** | Export/import `.dwf` bundles — workflow + recordings + screenshots in one file |
| **Scheduling** | Hourly, daily, or weekly on specific days |
| **Agent Context** | One central context block for rules, credentials, conventions, and reusable guidance prepended to step prompts |

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
codex_plugin/        # Codex bridge plugin and reporting setup
cursor_plugin/       # Cursor bridge plugin and reporting setup
sidecar/             # Go binary — machine control agent (macOS/Windows)
vendor/ecc/          # Vendored ECC skills, harness docs, and command surface
assets/
├── avatars/         # Skin packs (Clippy, Nugget, Rusty, Masko, etc.)
└── readme/          # README images
docs/                # Hermes and implementation notes
tests/               # Property-based and unit tests
```

---

## Contributing

Suggestions, bug reports, and pull requests are welcome. Open an issue to discuss a change before sending a PR. Check existing issues first to avoid overlap.

## License

Licensed under the TENSOLOGY COMMUNITY LICENSE AGREEMENT. See [LICENSE.md](LICENSE.md) for details.

---

> **Note:** This project has no cryptocurrency or token associated with it. Any coin using the DecisionsAI name is not affiliated with us.
