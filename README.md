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
