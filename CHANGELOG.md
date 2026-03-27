# DecisionsAI Changelog

---

## [2.6.8] - 2026-03-27

### Security & Windows Installer Fixes

**LiteLLM Supply Chain Attack** – On March 24, two versions of the LiteLLM package (1.82.7 and 1.82.8) were published to PyPI with hidden malware that steals passwords, cloud credentials, and SSH keys. Both versions have been removed, but we've updated the installer to explicitly block them so they can never be installed — even from a local cache. If you installed DecisionsAI between March 24–25, delete your virtual environment and reinstall. [Full breakdown from The Register](https://theregister.com/2026/03/24/trivy_compromise_litellm).

**Windows DLL Fix** – Fixed a crash on Windows where the speech engine (Kokoro) wouldn't load because two conflicting versions of the same library were fighting each other. The installer now sets up the correct one first so there's no conflict.

**Dependency Pinning** – Pinned Pydantic to avoid a version that requires Rust to compile on Windows (most users don't have Rust installed). Pip is now auto-upgraded to the latest version before installing anything. All installs skip the pip cache to avoid stale packages causing failures, and the installer automatically clears the cache and retries if something goes wrong.

---

## [2.6.7] - 2026-03-27

### Windows Installer, Qwen3-TTS Removal, Bug Fixes

**Windows Installer** – The Windows installer now handles a lot more automatically: installs Git if it's missing, sets up C++ build tools for compiling native packages, adds DecisionsAI to your system PATH so you can run it from anywhere, and fixes the Windows 260-character path limit that was breaking some installs. If a package can't be compiled, the installer skips it and falls back to an alternative.

**Qwen3-TTS Removed** – We removed Qwen3-TTS as a voice option. It sounded good in demos, but in real conversations the latency was too high — you'd be waiting a couple seconds for every response. Kokoro (offline, fast) and ElevenLabs (cloud, high quality) are the recommended options for text-to-speech and voice cloning.

**Telegram** – Voice notes sent through Telegram now use whatever voice you've picked in your chat settings instead of a default. The welcome message gets forwarded to Telegram when you connect. On Mac, the system stays awake while Telegram is connected so the bot doesn't go silent.

**Google** – You can now disconnect Google with a button (instead of manually deleting files). Connected services show a checkmark so you can see at a glance what's linked.

**Voice & Mouse** – Fixed "move mouse to center" accidentally taking a screenshot instead. Fixed audio glitches in push-to-talk where blank audio artifacts would show up as text. Fixed the agent randomly dying every 5 minutes due to an idle timeout.

**Other** – Create multiple tickets from one voice command. Config folder unified to one location. App shows version info at startup. Various UI fixes.

---

## [2.6.6] - 2026-03-25

### Oracle Skins System — Your Assistant, Your Way

This one's huge. Remember swapping Winamp skins back in the day and completely changing how your music player looked and felt? We brought that energy to DecisionsAI. The oracle window now has a full skin system — pick a character, and the entire look and behavior of your assistant changes instantly.

**Skin Gallery** – The Skins tab in Preferences shows every available skin as a big visual card with a live preview. Click one and it switches instantly. Oracle stays round with the glow ring. Avatars go square and transparent — the character floats on your desktop with no background.

**Event Hooks** – Each avatar skin maps application events (thinking, listening, working, needs attention) to different animations. When the AI is processing, your avatar switches to its thinking animation. When it needs your input, it switches to the attention pose. All configurable per-hook in the skin editor.

**Ping-Pong Playback** – WebM and GIF animations can play forward-then-backward in a smooth loop, or just loop normally. It's a per-event setting in the skin config, so idle can ping-pong while other states restart from frame 0.

**Chroma-Key Transparency** – Avatar WebM files have their background color automatically detected and removed at load time. Each skin specifies its background color in the config, and the system removes it with smooth edge falloff — no harsh cutoffs. The result: characters float transparently on your desktop.

**Skin Config Files** – Every skin is a `skin.json` in its avatar folder. It defines the rendering shape (round/square), border, shadow, glow behavior, image scale, chroma-key color, and the full event-to-animation mapping. Drop a new folder with a `skin.json` and it appears automatically.

**Oracle GIF Picker** – The oracle skin has 20 different animated GIF backgrounds. Pick one from the dropdown in the Skins tab and it updates live.

**GlowEngine** – The glow ring on the oracle is now driven by a dedicated engine with four styles: breathing (sinusoidal), pulse (PTT), fade (InOutCubic for dictation), and flash (file drop success). All configurable per-event in the skin config.

**Static Image Skins** – Skins can use PNG/JPG/WebP images instead of animations. Hayley uses three PNGs (idle, thinking, processing) that swap based on what the AI is doing.

**Context Menu Naming** – The right-click menu now says "Hide Clippy" / "Show Clippy" instead of "Hide Oracle" — driven by the skin's name field.

**Settings Migration** – Existing users' oracle settings (GIF filenames like "0.gif") are automatically migrated to the new skin system on first launch. No manual steps needed.

**Bug Fixes** – Fixed PTT getting stuck after dragging. Fixed glow not clearing on release. Fixed animation flickering from dual rendering systems. Cleaned up ~200 lines of dead legacy code from the oracle window.

---

## [2.6.0] - 2026-03-23

### Codebase Overhaul, Step Runner, Playwright, Telegram Remote Control

Big internal cleanup — the entire project was reorganized, about 15,000 lines of dead code removed, and the folder structure actually makes sense now. Over 200 files were touched. If you're reading the code, it's way easier to navigate.

**Step Runner Overhaul** – You can now bind actions directly to steps, so a step can trigger a saved macro instead of just sending text. The whole run flow is more intuitive — start, pause, skip, cancel all work the way you'd expect. Pass/fail routing and agent-decided routing are reliable now.

**Playwright Browser Tool** – The agent can open a headless Chrome browser, run scripts, and actually see the result. It captures a full-page screenshot and all console logs (errors, warnings, failed requests), then sends both to the vision LLM. If a page is blank and the console has a 500, the agent catches that.

**Telegram Remote Control** – Type "remote" in Telegram and you get an HMAC-encrypted link to a full web UI for your machine. Screenshots stream live, you can view multiple screens, drag and drop with the cursor, upload and download files, even move files between machines. Chats, actions, step runner, snippets — all controllable from your phone. Each session is token-protected with rate limiting and SSRF prevention.

**Bug Fixes & Performance** – Fixed a startup crash with agent persona loading. Fixed voice personality not carrying over to custom voices. Removed unnecessary database calls and stale imports. The app loads faster. 

---

## [2.5.0] - 2026-03-21

### REST API, Chat Hotswap, Vision Fix

**REST API** – Control DecisionsAI from scripts, curl, or any HTTP client. Create chats, send messages, run actions, trigger step runner sessions — all at `http://127.0.0.1:8765/api/`. Built-in docs page at `/docs/` with live testing. The agent knows the API too, so you can ask it for the right curl command.

**Chat Hotswap** – Switching chats no longer restarts the agent. Model and voice swap on the fly, history flushes without reloading the whole pipeline. Way faster.

**Better Defaults** – Fresh installs ship with qwen3:8b, qwen2.5-coder:7b, qwen3-vl:2b, and Kokoro Heart. No more outdated llama references.

**Vision Just Works** – Pick a vision model in settings and the app trusts your choice. No more popups or silent swaps.

**Bug Fixes** – Fixed first-launch crash, push-to-talk dying after chat switch, voice going silent after tool use, tool call text leaking into chat, audio stream crashes, vision not working with some providers, speaker volume not restoring.

---

## [2.4.0] - 2026-03-20

### Voice Cloning, Screen Intelligence, and Audio Improvements

**Clone Any Voice** – Create custom voices by uploading a short audio clip (WAV, MP3, M4A, or WebM) or recording one directly in the app using the built-in wizard. Pick Male or Female for better results, give it a name, and your cloned voice appears in the dropdown with a ⭐ prefix. Kokoro cloning runs entirely on your machine — no cloud, no limits. ElevenLabs cloning is also supported and tends to retain accents more faithfully. Works in live conversations, chat previews, Telegram voice notes, and saved audio. Create, edit, and delete custom voices from both Settings and the Chat UI without leaving what you're doing.

**Screen Intelligence** – The vision system has been rebuilt from the ground up into a modular pipeline that handles 20 distinct use cases. Ask the agent to describe your screen, find a button, click an element, read an error, check if a toggle is on, count open tabs, identify the active app, scroll to a section, navigate menus, fill in forms, and more. It layers OCR text detection, OpenCV element recognition, and vision LLM analysis — picking the fastest path for each request. When it moves your mouse to a target, the cursor follows a natural curved path instead of teleporting.

**Cleaner Audio Pipeline** – Echo cancellation subtracts speaker output from mic input in real time, and an energy-aware gate only lets your voice through when it's clearly louder than the speaker bleed. Interrupts now cut audio immediately with no overlapping or garbled speech. Hands-free mode is dramatically more reliable, especially on laptops.

**Better Pronunciation** – Common English contractions (I'm, don't, you're, etc.) are now expanded before reaching the speech engine, fixing cases where words like "I'm" sounded like "imm."

---

## [2.3.1] - 2026-03-18

### Custom Voice Cloning, Windows Support, and Fixes

**Custom Voice Cloning** – Clone voices with ElevenLabs and Qwen3-TTS. Upload audio clips in Settings, transcription is auto-filled via Whisper, and the cloned voice appears in the voice dropdown with a ⭐ prefix. ElevenLabs uses Instant Voice Cloning (IVC) with a 5-voice limit. Qwen3-TTS clones at inference time using reference audio — no limit. Delete custom voices from the dropdown with the trash icon. Custom voices work in both the web preview player and the live agent pipeline.

**Dynamic TTS Provider Registry** – Voice providers and their voices are now served from a single registry (`TTS_PROVIDERS` in constants). The UI populates dropdowns dynamically from `/api/tts/providers` — nothing hardcoded in JS or HTML.

**Windows Support** – Added `decisions.bat` and `decisions.ps1` launchers for Windows. Same auto-setup flow as macOS: checks Python 3.12, creates venv, installs deps, downloads models, and starts the app.

**Qwen3-TTS Fixes** – Forced CPU device on Apple Silicon (MPS crashes on grouped-query attention). Float32 dtype for non-CUDA. Model downloads to local `models/qwen3-tts/` directory instead of HF cache. Suppressed flash-attn warnings and HF progress bars. Hot-swap now updates voice in-place without reloading the 600M parameter model.

**Settings UI** – Settings gear icon in the header now shows active state. Removed Replicate from third-party API section. Coqui TTS commented out (no Python 3.12 support).

---

## [2.3.0] - 2026-03-18

### Step Runner Overhaul, Qwen3-TTS, and Chat Fixes

**Step Runner** – Big upgrade. Steps now show real status (running, done, failed) instead of marking everything done right away. You can cancel a run or skip a stuck step. The agent knows which step it's on and what the goal is. Steps that hang for 5 minutes get marked failed and the run continues. Missed scheduled runs? They'll run when you start the app.

**Qwen3-TTS** – New local voice option. Install the package, pick it in Settings, and choose from voices like Aiden, Ryan, Vivian, Emma. Runs on your computer.

**Chat & Voice** – Chat switching is more stable. Single-step runs show real agent replies instead of "Instruction sent." Failed runs are marked failed, not completed. Push-to-talk and TTS timing fixed. When the agent can't find something on screen, it asks you to bring it into view instead of guessing.

---

## [2.2.0] - 2026-02-08

### Browser-Based UI and Code Cleanup

Everything now lives in browser tabs – Settings, Actions, Snippets, and Projects. No more separate windows. Switch between them instantly.

The codebase got a major cleanup. Tools and utilities are organized better, so it's easier to understand and work with.

**Playwright Investigation** – Started exploring headless browser automation with Playwright for automated testing workflows. Plan, execute, validate, repeat.

**OpenRouter** – Unified LLM API access through OpenRouter, giving you one key for dozens of models from different providers.

---

## [2.1.9] - 2026-01-29

### Vision Support for All Providers

You can now send images to any LLM provider – Anthropic, OpenRouter, Groq, KiloCode, and Ollama. If the model supports vision, it works. Images are auto-compressed to WebP to save bandwidth and cost. All the existing vision tools (screenshots, image analysis, etc.) work with every provider.

---

## [2.1.8] - 2026-01-26

### Dependency Updates and Bug Fixes

**Dependency Refresh** – Updated pipecat-ai, langchain, litellm, mcp, sqlalchemy, llama-index, and elevenlabs to their latest versions. PyTorch import is now optional in actions.py to avoid compatibility issues on macOS with Python 3.12.4.

**Bug Fixes** – Fixed signal_manager import path, database migration errors, and improved error handling for optional dependencies.

---

## [2.1.7] - 2026-01-19

### Project Management and Voice-Controlled Switching

**Voice-Controlled Projects** – Say "Open project Tensology and start it" and the agent switches to that project, opens it in Cursor or VS Code, and generates startup files. Fuzzy matching handles speech-to-text typos, so "Tensorlogy" still finds "Tensology." After opening a project, the agent asks what you'd like to do rather than guessing.

**Duplicate Instance Detection** – The app now detects and terminates multiple running instances automatically, preventing resource conflicts.

**UI Improvements** – Better project management layout with context items and file associations. The ticket board got drag-and-drop improvements, better status management, and cleaner visuals. Audio device hot-swapping works more reliably with automatic fallback.

**Fixes** – Python 3.10 compatibility restored. Kokoro TTS requirements updated. Fixed a bug where the wrong project would activate due to empty tool parameters. Setup script improvements.

---

## [2.1.6] - 2026-01-13

### STT Streaming Updates

**AssemblyAI** – Migrated to the v3 streaming API for hands-free mode, fixing deprecated model errors.

**OpenAI Whisper** – Added Realtime API support for hands-free streaming with batch API fallback for push-to-talk.

**Hot Reload** – Changing your STT, TTS, or LLM model now triggers a full agent reload so the new config actually takes effect. All STT services display the active model name in transcription output.

---

## [2.1.5] - 2026-01-03

### Telegram Remote Control Improvements

**WebP Compression** – Screenshots sent to Telegram are now converted to WebP at 80% quality, cutting file sizes by 25-35%. Vision LLM images get the same treatment for faster processing.

**More Remote Commands** – Double-click support, keyboard shortcuts (select all, copy, paste), extra navigation keys (up, down, enter, page up, page down, break), and a new "instruction" command that sends text directly to the agent as if it came from Telegram.

**Quieter Logs** – Connection polling and routine status updates no longer spam the logs. Auto-reconnects don't send "online" messages to Telegram anymore, so you won't get notification spam during brief network hiccups.

**Stability** – Fixed BrokenPipeError and ConnectionRefusedError during shutdown. Added missing stop() method to ActionPlaybackService. Better WebSocket idle handling, message queuing, and reconnection logic.

---

## [2.1.4] - 2025-12-27

### Telegram Integration and File Safety

**Telegram Bot** – Connect your Telegram account and control the agent with voice messages or text commands. Say "remote control" or "remote" in Telegram to open a web-based interface for navigating your computer screens over WebSocket.

**File Operation Safeguards** – Every file operation now has confirmation dialogues and safety gates to prevent accidental data loss or unauthorized modifications.

**Direct Type Command** – Say "type 'hello world'" for immediate keyboard input without LLM processing. Also supports "type from clipboard."

**Other Additions** – FLAC and other audio format conversions. Cursor ticket creation for development integration. Improved tooling clarity so commands route to the right place.

---

## [2.1.2] - 2025-12-21

### Google Workspace Integration

**Full Google Suite** – Direct integration with Google Calendar, Docs, Drive, Sheets, and Gmail through OAuth 2.0. Create events, check your schedule, create documents from markdown, list and upload files, read PDFs, check your inbox, send emails, create drafts, reply, and delete. Filter emails by type (inbox, sent, drafts, starred, important, unread, trash, spam).

**Smart Routing** – When Google is connected, "email" always means Gmail. Google Workspace takes priority over Rube for all Google services. Real-time connection testing with streaming results and automatic API enablement reminders.

**Fixes** – Draft email creation works reliably now. Inbox shows all emails by default (not just unread). Fixed system prompt formatting errors that prevented startup. Better token storage and validation for OAuth.

---

## [2.1.1] - 2025-12-20

### Rube.app Integration

**500+ App Connections** – Rube.app integration brings cross-app automation for Google Workspace, Notion, Slack, GitHub, and hundreds more. Set it up once and the agent can interact with all of them.

**About Window** – New tabbed interface with a built-in Changelog viewer. Enhanced emoji support and text replacements. Cleaner styling and layout. Fixed nested scrollbars in the changelog display.

---

## [2.1.0] - 2025-12-10

### OpenRouter and Anthropic Support

**OpenRouter** – Unified LLM API access through OpenRouter, giving you one key for dozens of models from different providers.

**Anthropic Claude** – Direct Claude API support. Enhanced vision capabilities across multiple providers. Improved chat interface with model selection. Better error handling for API connections.

---

## [2.0.0] - 2025-11-25

### Complete UI Overhaul and Tool Ecosystem

**New Interface** – Complete redesign with a modern dark theme. The actions system was rebuilt from scratch for reliability, and the tray menu now reflects recording state and playback status.

**40+ Tools** – Comprehensive tool ecosystem covering file operations, document processing, audio transcription with speaker diarization, image generation, snippet management, and Cursor ticket creation.

**Performance** – Improved streaming performance, better memory management, and an enhanced tool loading system.

---

## [1.5.0] - 2025-10-28

### Web Search, Vision, and Code Execution

**Web Search** – The agent can now search the web and bring back results.

**Vision Tools** – Screenshot analysis with vision models, plus a dedicated vision analyzer for image understanding and an image generator.

**Document Tools** – PDF page extraction and document text extraction for working with files hands-free.

**Code Execution** – Run Python code and scripts directly through the agent. New system information tool and type-text tool for clipboard input.

**Stability** – Better error handling across the board. Fixed memory leaks in long-running sessions.

---

## [1.4.0] - 2025-09-20

### Multi-Provider Voice and LLM Support

**Speech-to-Text** – AssemblyAI integration with real-time streaming transcription and speaker diarization. OpenAI Whisper STT support added as an alternative.

**Text-to-Speech** – ElevenLabs and OpenAI TTS integration. Multiple voice options across providers.

**LLM Switching** – Switch between LLM providers on the fly. Chat history now persists between sessions. Model hot-reloading means you don't have to restart the app to change models.

---

## [1.3.0] - 2025-08-15

### Expanded Tool Set

**File and Document Tools** – Fast file management, document extraction, and audio transcription tools.

**Clipboard and Snippets** – Clipboard actions, rework and summarize clipboard content, create and use reusable snippets, and save audio from conversations.

**Oracle Globe** – New globe control tool for the visual interface. New chat and clear chat tools for managing conversations.

**Fixes** – Clipboard operations no longer block the UI. TTS feedback timing improved.

---

## [1.2.0] - 2025-07-22

### Chat Window and Input Tools

**Chat Interface** – Full conversation history with streaming response display. Model selector with provider switching. Message action buttons for copy and audio playback. Built-in chat search.

**Input Tools** – Mouse movement and click tools, caret movement, text editing, media controls, and function/special key support. Redesigned chat interface with better conversation flow.

---

## [1.1.0] - 2025-06-25

### Whisper.cpp and Dictation Mode

**Whisper.cpp** – Replaced Vosk with Whisper.cpp as the primary STT engine. Real-time streaming recognition with significantly better speed and accuracy.

**Dictation and Transcription** – Dictation mode for voice typing and transcription mode for clipboard capture. Improved voice activity detection.

**Audio Controls** – Playback controls, save text as audio, and navigation tools (open window, shortcuts). Push-to-talk and continuous mode switching. Fixed audio playback speed issues and duration calculations.

---

## [1.0.0] - 2025-05-15

### Major Rebuild with Pipecat

**New Architecture** – Complete rebuild using the Pipecat framework for real-time voice AI. Frame-based architecture with 40-50% memory reduction and significantly improved latency.

**Voice Modes** – Hands-free continuous listening and push-to-talk. Real-time streaming responses with interruption handling.

**Providers** – Vosk STT, Kokoro and ElevenLabs TTS, Ollama and OpenAI LLM support.

**Controls** – Oracle/Globe visual interface, voice commands, mouse and keyboard control, window management, media controls, and basic action recording.

---

## [0.9.0] - 2025-04-20

### Early Voice Commands

Basic voice command recognition with Vosk. Simple text-to-speech output, window opening commands, clipboard operations, and basic action recording. Fixed many crashes, audio playback problems, and Vosk integration issues.

---

## [0.8.0] - 2025-03-25

### Mouse and Keyboard Control

Mouse control commands and keyboard shortcut support. Basic file operations. Vosk model download and setup. Improved command recognition and error messages.

---

## [0.7.0] - 2025-03-05

### First Chat Interface

Basic chat interface with conversation history and simple AI responses through Ollama. Vosk speech recognition integration. Fixed memory leaks, UI freezing, and Vosk crashes.

---

## [0.6.0] - 2025-02-15

### Settings and Configuration

Settings window with configuration persistence and model selection. Vosk STT configuration. Fixed settings not saving and configuration errors.

---

## [0.5.0] - 2025-02-01

### First GUI

Basic GUI window with simple voice input through Vosk and text output display. Fixed application crashes on startup and unreliable voice recognition.

---

## [0.4.0] - 2025-01-20

### Initial GUI Implementation

First GUI window structure with About window. Experimental Vosk speech recognition. Fixed command-line-only mode issues.

---

## [0.3.0] - 2025-01-15

### Basic Voice Recognition

First voice recognition attempt with Vosk, simple command execution, and text output. Fixed frequent crashes, command parsing errors, and Vosk integration problems.

---

## [0.2.0] - 2025-01-10

### Foundation

Initial tool system with basic command structure and file operations foundation. Vosk dependency setup. Early-stage stability work.

---

## [0.1.0] - 2025-01-05

### Project Structure

Project structure, basic dependencies, and initial codebase with Vosk speech recognition library integration.

---

## [0.0.1] - 2025-01-03

### Initial Release

Basic project setup with minimal voice recognition through Vosk. Early prototype.

---

## Notes

DecisionsAI is like having a smart assistant that can control your computer with your voice. Think of it like Siri or Alexa, but it can actually DO things on your computer - not just answer questions. 

**What makes it special:**
- You can talk to it naturally, like talking to an agent
- It can control your mouse and keyboard with voice commands
- It can help with tasks by explaining things, summarizing text, and even writing code
- It can take a screenshot of your screen and tell you what's on it.
- It works mostly offline (if you use Ollama), so you don't need internet
- It can automate boring tasks like sending emails or creating documents
- It integrates with 500+ apps through Rube.app

**Common Use Cases:**
- **Writing Papers**: Use dictation mode to speak your essay, then ask the AI to improve it
- **Research**: Ask the AI to search the web and summarize information
- **Coding**: Ask the AI to write code or help debug programs
- **Accessibility**: Great for people who have difficulty typing or using a mouse
- **Productivity**: Automate repetitive tasks like organizing files or sending emails

**Getting Help:**
- Say "what can you do?" to see all available features
- Check the Settings window to configure AI models and speech recognition
- The chat window shows your conversation history and lets you type messages too

---

*For more information, visit [tensology.com](https://www.tensology.com) or [decisionsai.net](https://www.decisionsai.net)*

---

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
