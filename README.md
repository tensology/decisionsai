# DecisionsAI

> **Your computer, controlled by voice.** Talk to it. It thinks, speaks back, and acts — opening apps, editing text, running macros, and managing your workflows — all on your machine, with your choice of AI.

<p align="center">
  <img src="assets/readme/example.webp" alt="DecisionsAI UI" width="900" height="300">
</p>

---

## What is DecisionsAI?

DecisionsAI is a **voice-first desktop assistant** for Windows and Mac. Speak naturally, and it listens, reasons with an AI model, replies out loud, and carries out what you asked — hands-free.

Under the hood, it runs on the [Pipecat](https://github.com/pipecat-ai/pipecat) real-time audio pipeline, which streams everything in small chunks. That keeps the app snappy, light on memory, and responsive enough to interrupt mid-sentence.

**Think: smart speaker meets desktop power-user tool — running entirely on your machine.**

You don't need to write code to use the core voice and chat features. You do need some patience on first setup, but that's a one-time cost.

---

## Why people use it

- **Stays private by default.** Everything runs locally until you say otherwise. Offline speech recognition ([Whisper.cpp](https://github.com/ggerganov/whisper.cpp)), a local LLM ([Ollama](https://ollama.ai/)), and offline text-to-speech ([Kokoro](https://github.com/thewh1teagle/kokoro-onnx)) — nothing leaves your machine unless you add API keys.

- **Works with every major AI provider.** OpenAI, Anthropic, ElevenLabs, or [OpenRouter](https://openrouter.ai/) (a single hub for GPT-5.4, Gemini 3 Flash, Claude, and whatever drops next). You're never locked in.

- **Control your PC from your phone.** Connect Telegram, send a voice message or text, and your desktop responds — commands, replies, screenshots, the works.

- **Google Workspace, natively.** Gmail, Calendar, Drive, Docs, and Sheets — direct API access, not routed through a third party. Fast and reliable. For Slack, GitHub, Notion, Teams, and 500+ more, use Rube/Composio automation.

- **Files and documents, not just chat.** Transcribe audio and video, pull context from PDFs and Word files, upload to Drive, convert Markdown to Google Docs, and capture dev tickets while you describe what you need.

- **It can see images.** Share a screenshot, photo, or diagram and the assistant can reason about it. Images are auto-compressed to WebP to keep requests light and cheap.

- **Record once, replay forever.** The Actions system captures keyboard and mouse sequences with precise timing. Say "run action" and replay the whole thing — perfect for repetitive tasks, form-filling, or app workflows.

- **Skins — like Winamp, but for your AI.** Remember swapping Winamp skins and completely changing how your music player looked? Same energy. Pick from animated avatars like Clippy, Nugget, Rusty, Masko, or Madame Patate — each with their own idle, thinking, working, and attention animations. Or stick with the classic Oracle orb. Skins aren't just cosmetic — each one maps application events to different animations and behaviors. The avatar reacts to what the AI is doing. Drop a new folder with a `skin.json` and your custom skin appears automatically.

<p align="center">
  <img src="assets/readme/chat.webp" alt="DecisionsAI UI">
</p>

## The local web interface

When DecisionsAI is running, it spins up a **local-only** web UI on your machine (not exposed to the internet). Open it from the Oracle menu or the in-app tray.

From there you can:

| Section | What you do there |
|---|---|
| **Preferences** | Open from the Oracle context menu (**Preferences**) or the web header gear; choose models and voices, add API keys (**Third Party Providers**, **LLMs**), connect Google and Telegram (**Advanced**), tune behavior |
| **Skins** | Browse and swap avatar skins in a visual gallery — pick your character, customize event-to-animation mappings, adjust size |
| **Chat** | Browse and manage conversation threads, switch between chats |
| **Actions** | View, edit, rename, and trigger recorded macros |
| **Snippets** | Manage text or code snippets with trigger words you invoke by voice |
| **Projects** | Create projects with context blocks and linked files; set trigger words so the assistant knows which project you mean |
| **Step Runner** | Plan multi-step workflows with validation, agent-based routing, recording, presets, and scheduling — run them one at a time or all at once |

---

## Performance & architecture

All audio, text, and control signals flow through **[Pipecat](https://github.com/pipecat-ai/pipecat)** — a real-time pipeline that passes small **frames** between speech-to-text, the LLM, and text-to-speech in sequence.

The result:

- **Lower memory usage.** Streaming in chunks instead of loading full buffers can cut RAM footprint by 40–50% compared to naive pipelines.
- **Faster back-and-forth.** Small frames move through the stack quickly, making voice conversations feel snappy.
- **Natural interruptions.** Cut the assistant off mid-sentence — the pipeline is built for it.
- **Kinder to modest hardware.** No giant spikes when a long reply is generated; work is chunked and spread out.

### Technology stack

**Offline core:**

| Component | Role |
|---|---|
| [Whisper.cpp](https://github.com/ggerganov/whisper.cpp) | Fast, accurate offline speech recognition |
| [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) | High-quality offline text-to-speech, plus **custom voices** from your audio (on-device conversion) |
| [Ollama](https://ollama.ai/) | Local LLM inference (Llama, Gemma, and more) |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | Real-time voice pipeline orchestration |

**Optional cloud services:**

| Service | What it adds |
|---|---|
| [OpenRouter](https://openrouter.ai/) | Unified access to GPT-5.4, Gemini 3 Flash, Claude, and every new model as it releases |
| [OpenAI](https://openai.com/) | GPT-5.4, GPT-4 Turbo, GPT-4o |
| [Anthropic](https://www.anthropic.com/) | Claude 3.7 Sonnet, Claude 3.5 Opus, Claude 3 Haiku |
| [ElevenLabs](https://elevenlabs.io/) | Cloud TTS with voice cloning (up to 5 custom voices via Instant Voice Cloning) |
| [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) | Local TTS with strong cloning quality and **unlimited** custom voices; **much slower** than Kokoro for many setups |
| [AssemblyAI](https://www.assemblyai.com/) | Advanced transcription and speech recognition |
| [Rube/Composio](https://composio.dev/) | Connect to 500+ apps for workflow automation |

---

## System requirements

### Offline / local mode

| | |
|---|---|
| **OS** | macOS (Apple Silicon & Intel), Windows, Linux |
| **RAM** | 8 GB minimum; 12 GB recommended for the default qwen3:8b model |
| **Python** | 3.12 |
| **System deps** | PortAudio, FFmpeg |
| **Disk** | ~6 GB free for initial model downloads |

> **Adaptive model selection:** DecisionsAI detects your system RAM at first launch and automatically picks an Ollama model that fits. On an 8 GB machine it pulls `qwen3:1.7b` (~1.5 GB); on 12+ GB it pulls the full `qwen3:8b` (~5 GB). You can always switch models later in **Preferences → LLMs**.

| RAM | Default model | Approx. VRAM |
|---|---|---|
| ≤ 9 GB | `qwen3:1.7b` | ~1.5 GB |
| 10–11 GB | `qwen3:4b` | ~3.5 GB |
| 12–23 GB | `qwen3:8b` | ~6 GB |
| 24–47 GB | `qwen3:14b` | ~10 GB |
| 48+ GB | `qwen3:32b` | ~22 GB |

**First-run downloads (varies by RAM):**

| Component | Size | Time at 100 Mbps |
|---|---|---|
| Kokoro TTS models | ~100 MB | < 1 min |
| Ollama model (auto-selected) | 1–20 GB | 1–30 min |

> Slow connection (10 Mbps)? Budget over an hour. Progress bars are shown throughout.

<p align="center">
  <img src="assets/readme/about.webp" alt="DecisionsAI UI">
</p>


### Online / cloud mode

Using OpenAI, Anthropic, or OpenRouter brings requirements down dramatically:

| | |
|---|---|
| **RAM** | 4 GB minimum, 8 GB recommended |
| **Disk** | ~200 MB (lightweight local components only) |
| **Internet** | Stable, fast connection required |

**Keys worth adding:** open **Preferences** from the Oracle context menu (or the gear icon in the local web UI). Under **Third Party Providers**, add your **OpenAI** and **OpenRouter** API keys. That unlocks **vision** with supported models, plus the full range of models and features those providers expose. OpenRouter also gives you one place to try many vendors; OpenAI covers GPT-family vision and tools where you want first-party access.

No large model downloads. Models run in the cloud; only Whisper.cpp and Kokoro install locally. You still get offline voice I/O — just cloud reasoning.

> **Mix and match:** use local models for sensitive tasks, cloud models for heavy reasoning. DecisionsAI routes based on your configuration.

---

## Installation

### Quick start (recommended)

The bundled launchers handle everything: dependency checks, Python setup, model downloads, and launch.

```bash
git clone https://github.com/tensology/decisionsai.git
cd decisionsai
```

Then run the launcher for your platform:

| Platform | Command |
|---|---|
| **macOS** | Double-click `decisions.app`, or run `./bin/decisions.sh` |
| **Windows** | Double-click `bin/decisions.bat`, or run `bin/decisions.ps1` from PowerShell |
| **Linux** | `./decisions` |

Each launcher automatically: checks and installs system dependencies, sets up a Python virtual environment, installs packages from `requirements.txt`, downloads AI models, and starts the app.

> On Linux/macOS, system dependency installation may require `sudo`. On Windows, the script will use winget, Chocolatey, or Scoop if available.

---

### Manual installation

Prefer full control? Run the scripts in `bin/` yourself.

#### Prerequisites

- Python 3.12
- PortAudio and FFmpeg
  - **macOS:** `brew install portaudio ffmpeg`
  - **Linux:** `sudo apt-get install portaudio19-dev ffmpeg`
  - **Windows:** install via winget, Chocolatey, or Scoop

#### Step 1 — Python environment

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

#### Step 2 — Dependencies

```bash
# Python 3.13+ only: set this flag first
export PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

pip install -r requirements.txt
```

#### Step 3 — Download AI models

```bash
python bin/setup.py
```

This downloads:
- **Kokoro TTS** — `kokoro-v1.0.onnx` (~50 MB) + `voices-v1.0.bin` (~10 MB) → `./distr/core/agent/models/`
- **Ollama LLM** — `llama3.1:8b` (~4.9 GB) via the Ollama API

The script checks whether models already exist before downloading, shows progress bars, and can be re-run to resume failed downloads.

#### Step 4 — Start

```bash
python bin/start.py
```

---

### Optional components

**Vosk (alternative STT)**

Whisper.cpp is the default. If you prefer Vosk:

```bash
python bin/setup_vosk.py
```

Downloads the English model (~1.8 GB) to `./distr/core/agent/models/vosk-model-en-us-0.22/`. Switch between engines in **Preferences** → **LLMs** → **Speech to Text (STT)** → **Model**.

**Qwen3-TTS (alternative TTS)**

Kokoro is the default. Qwen3-TTS installs automatically with `pip install -r requirements.txt` and downloads models from HuggingFace on first use. Switch to it in **Preferences** → **General** (TTS **Provider** / **Voice**) and choose from voices like Aiden, Ryan, Vivian, or Emma.

**Expect slower playback.** Qwen3-TTS often delivers **excellent** cloned and preset voice quality, but speech generation can be **much slower** than Kokoro (and usually slower than cloud TTS). Use it when quality matters more than how fast the assistant talks.

**Custom voice cloning**

**Kokoro**, **ElevenLabs**, and **Qwen3-TTS** can clone a voice from audio clips:

1. Select **Kokoro**, **ElevenLabs**, or **Qwen3-TTS** as your TTS provider in **Preferences** → **General**
2. Click **+ Custom** next to the voice dropdown
3. Upload one or more audio clips. Transcription is filled in automatically via Whisper where the flow uses it
4. The cloned voice appears in the dropdown with a ⭐ prefix, ready to preview and use

| Provider | Notes |
|---|---|
| **Kokoro** | Stays **offline**: reference audio is combined with on-device conversion so your custom voice does not need a cloud clone API |
| **ElevenLabs** | Instant Voice Cloning API (**max 5** custom voices) |
| **Qwen3-TTS** | **No fixed cap** on custom voices; cloning runs at inference time. Same **speed warning** as above: great quality, often **very slow** compared with Kokoro |

---

## IDE integration (Kiro)

**[Kiro](https://kiro.dev)** is the path we recommend with DecisionsAI: describe a feature, bug, or refactor out loud, and the assistant turns that into structured tickets and context your editor can pick up without you retyping the brief.

If you use **Visual Studio Code** or **Cursor**, there is also a **VS Code extension** that pairs with DecisionsAI for the same voice-to-ticket workflow.

---

## Integrations

### Telegram

Connect your Telegram account in **Preferences** → **Advanced** → **Telegram** (opens the link / QR flow). Once connected:

- **Send voice or text** to your bot and get replies, voice notes, and screenshots back
- **Type "remote"** in Telegram to get an **HMAC-encrypted link** to the remote control web UI — a full browser-based interface for controlling your machine from anywhere
- **Stream your screen live** — screenshots are streamed in real time, and you can view more than one monitor at once
- **Drag and drop** with the cursor, click, type, scroll, and run keyboard shortcuts — all from your phone or any browser
- **Transfer files between machines** — upload files to your desktop or download files from it, right through the remote UI. You can move files between your phone and your computer (or between two computers) via Telegram
- **Full app control** — manage chats, actions, step runner workflows, and snippets without touching the desktop. If Decisions is open, the remote UI can do everything the local UI can
- Connections reconnect silently with exponential backoff, without spamming your chat

**Remote control security:**

The remote control link is HMAC-encrypted so only the person who requested it can open it. Each session generates a cryptographically random API token — WebSocket connections and HTTP requests must present it. Origin validation restricts access to localhost. Outbound URL validation blocks SSRF attempts against private/local network targets. API keys are masked in all responses. A sliding-window rate limiter prevents brute-force attempts.

### Google Workspace

Connect via **Preferences** → **Advanced** → **Google** (OAuth 2.0; Gmail, Calendar, Drive, Docs, Sheets).

| Service | What you can do |
|---|---|
| **Gmail** | Read, send, draft, and reply to emails |
| **Google Calendar** | Create events, check your schedule, manage appointments |
| **Google Drive** | List folders, read files, upload documents, access PDFs |
| **Google Docs** | Create documents directly from Markdown |
| **Google Sheets** | Read and interact with spreadsheet data |

Direct API access means faster, more reliable responses than routing through third-party automation.

---

## Voice commands

Speak naturally — exact phrasing can vary. Here's a reference of what DecisionsAI understands.

### Navigation & windows

| Say | Does |
|---|---|
| Open / Focus / Focus on `[app]` | Opens or focuses a window |
| Hide oracle / Show oracle | Toggles the overlay globe |
| New tab / Close / Quit | Tab and window management |
| Open spotlight | Cmd+Space |

### Text editing

| Say | Does |
|---|---|
| Copy / Paste / Cut / Undo / Redo | Standard edit commands |
| Select all | Selects everything |
| Delete line / Clear line / Force delete | Line-level deletions |
| Up / Down / Left / Right | Cursor movement |
| Page up / Page down / Home / End | Navigation |

### Mouse control

| Say | Does |
|---|---|
| Mouse up/down/left/right | Move mouse |
| Mouse slow `[direction]` | Fine mouse movement |
| Move mouse center/top/bottom/far left/far right | Jump to screen position |
| Click / Double click / Right click | Mouse clicks |
| Scroll up / Scroll down | Page scroll |

### AI assistant

| Say | Does |
|---|---|
| Dictate | Enter dictation mode — types what you say |
| Transcribe / Listen | Stores what you say to clipboard until "Enter this" or "stop listening" |
| Read / Speak / Recite | Reads out transcribed text, or selected text if you say "this" |
| Agent / Hey / Jarvis | Activates the AI agent for complex tasks |
| Explain / Elaborate | Explains clipboard content |
| Rework this / Reword this | Rewrites selected text via LLM, then pastes |
| Summarize this | Summarizes selected text, then pastes |
| What's in the clipboard | Shows clipboard content in chat |
| Save this as audio | Generates a WAV file on the Desktop from selected text |
| Translate | Translates clipboard text |
| Type "`text`" | Immediately types the specified text |

### Recorded macros (Actions)

| Say | Does |
|---|---|
| Start recording | Begin capturing keyboard and mouse input |
| Stop recording | Stop and name the recorded action |
| Run action `[name]` | Replay the recorded sequence |
| Stop action | Halt a running action immediately |

### Playback & media

| Say | Does |
|---|---|
| Pause / Stop / Play | Media playback control |
| Next track / Previous track | Track switching |
| Mute / Volume up / Volume down | Audio control |

### System control

| Say | Does |
|---|---|
| Start listening / Stop listening | Toggle voice recognition |
| Stop speaking / Shut up | Stop the assistant mid-reply |
| Press F1–F12 | Function keys |
| Space bar / Enter this / Escape / Tab | Special keys |
| Exit | Quit the application |

---

## Code execution

The assistant can run Python, touch files, and carry out multi-step jobs on your machine. Treat it like any local automation tool — use it on machines and accounts you trust.

---

## Step Runner

The Step Runner is where you build multi-step workflows that the agent executes in sequence (or in whatever order you tell it to). Think of it like a playlist of instructions — each step does one thing, checks if it worked, and decides where to go next.

### How it works

A **workflow** is a list of **steps**. Each step has:

- **An action** — what the step actually does. Usually an agent instruction ("open Safari and go to example.com"), but can also be a recorded macro (Play Recording), a shell command, variable assignment, or HTTP request.
- **Validation** — optional check after the step runs. Text matching, rule-based checks, LLM judgment ("did the page load?"), or screenshot comparison against a reference image.
- **Routing** — what happens after the step finishes. By default, the workflow ends. You can point it to another step on pass or fail, or let the agent decide dynamically.

### Routing modes

**Static routing** is the simple version: pick a "go to" step for pass and a different one for fail. Leave them blank and the workflow ends.

**Agent decision routing** is the interesting one. Instead of hardcoding the next step, you give the agent a prompt like "if the user is logged in, go to the dashboard step; otherwise go to the login step." The agent sees the step result, the pass/fail status, and a list of every other step in the workflow, then picks where to go. It can also choose to end the workflow entirely.

There's a safety net — if the agent tries to route a step back to itself, the workflow ends automatically. No infinite loops.

### Recording

Steps with the **Play Recording** type are dedicated to recorded macros. Set a step's type to "Play Recording" and the form switches from an instruction textarea to recording controls — record, stop, and play. Hit the record button, a 3-2-1 countdown overlay appears on screen, and then it captures your keyboard and mouse input. Stop recording and the file gets saved to the step. When the step executes (isolated or as part of a workflow run), it replays the recording automatically. This uses the same recording infrastructure as the Actions system.

### Presets

Workflows can be exported as `.dwf` files — a compressed bundle that packages the workflow definition, any step recordings (the JSON mouse/keyboard captures), and reference screenshots for validation all into one file. Think of it like a ZIP with a custom extension.

From the web UI, click the 📋 icon next to the search bar to see available presets and load them with one click. Use 📤 Export to save a workflow to the `steprunner/presets/` directory, or ⬇ Download to grab the `.dwf` file directly. The import option in the presets dropdown accepts both `.dwf` bundles and plain `.json` files.

When you import a bundle, recordings get extracted to the recordings directory and screenshots get placed where they need to be — everything wires up automatically. Share presets with your team by dropping files into `steprunner/presets/`.

### Scheduling

Each workflow can be scheduled to run on a timer — hourly, daily, or weekly on specific days. Set the time, pick the days, and the workflow kicks off automatically. Good for things like "check my calendar every morning" or "run this health check every hour."

### Variables

Workflows support variables that persist across steps within a single run. Define them in the Variables tab with a name and default value. Steps can read and write these during execution.

---

## Contributing

Suggestions, bug reports, and pull requests are welcome. Open an issue to discuss a change before sending a PR. Check existing issues first to avoid overlap.

## Development status

Active development. Current focus:

- Improved voice recognition accuracy
- Stronger offline capabilities
- Support for additional AI models
- Enhanced dictation and transcription
- Step Runner scheduling and workflow planning

## License

Licensed under the TENSOLOGY COMMUNITY LICENSE AGREEMENT. See [LICENSE.md](LICENSE.md) for details.