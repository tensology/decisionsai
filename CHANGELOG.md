# DecisionsAI Changelog

---

## [What's to Come]

In the beginning it was prompt engineering, then skills, then Codex and Cursor harnesses that could report back from a project, and now Loops: workflows built to keep going until there is proof.

A loop is a named sequence of steps you import or build yourself. It takes a ticket, a WhatsApp thread, or a brief, plans the slice, hands work to chat or to Cursor/Codex/Pi on the linked project, checks whether the output actually matches the ticket, fixes what failed, and leaves evidence on the board. Not one answer and walk away. Iterate until green, or stop with a clear reason it blocked.

2.8 wired the surfaces together: boards, WhatsApp on boards, automations, calendar blocks, workflow steps, skills pushed into your coding tools, validation and steering on runs, IDE follow-up, and hooks for research, design, docs, and workplace apps. Most of the machinery is there. The work now is making the agent use it reliably without you babysitting an IDE.

What we are working on next:

- Loops that finish, and say plainly what failed when they do not.
- The agent noticing when a step is stuck instead of waiting for you to notice.
- Retries that use what already went wrong, not a blank "try again."
- Project context that survives between runs so you are not re-briefing every time.

Decisions should run the loop and report back. You should not have to watch Cursor and guess whether the ticket is actually done.

---

## [2.8.0] - 2026-06-14

### Web UI

- Main application menu tightened up across Chat, Projects, Ticket Boards, Automations, and Workflows
- Chat: switch LLM or voice in an open thread; Compact conversation history; tool and workflow events shown in the transcript
- Projects page: backend, folder, CLI setup, and project details on one screen
- Ticket Boards: hand a ticket off to agent chat with voice and linked project context
- IRC chat page and API proxy added alongside Telegram and WhatsApp
- Remote: Snippets, Agent, and Dictate brought forward; hold PTT, tap for text input, explicit stop on streamed audio
- Shortcuts: hold-to-dictate with modifier combos; system tray entries for common pages without opening the browser first
- Initiative settings grouped by channel (what the agent may scan, suggest, ask, or send)

### Workflows

- Loop presets: import, append, replace, or export workflow steps
- Steer a run while a step is waiting; active-run view shows validation, steering history, and next step
- New loop preset: Implement + Fallow Audit (JS)
- New step action `ytdlp`: YouTube metadata, subtitles, or search

### Automations

- Calendar view for scheduled runs
- Time-entry blocks with live timer; export timesheet-style summaries to boards

### WhatsApp

- Link a number to a board so inbound chats and ticket creation stay on that board

### Harness and IDE integration

- `bin/setup.py` and quiet recalibrate on `bin/start.py` project skills into Codex, Cursor, Claude, and Pi: ECC, Ponytail/Fallow, browser/content pack, design references, Agent Reach, community skills, yt-dlp, Composio Connect; RTK hooks; catalog at `~/.decisions/harness/mcp-recommendations.json`
- MCP recalibrate: merge Context7, Exa, Mobbin, Refero, Composio Connect into `~/.cursor/mcp.json` and `~/.codex/config.toml`; remove deprecated Rube; Composio API key from Settings → API Keys
- Ponytail and Fallow prepended on workflow `pre_chain` where applicable; Ponytail Cursor rule copied into the project on skill provision
- Codex/Cursor plugin verify and repair on setup; `plugins/` split into `codex-ide`, `cursor-ide`, `ecc`; scratch files under `.artifacts/`
- IDE threads: `ide_thread` tool and `GET /api/ide/sessions` to list, read, and prompt Codex/Cursor sessions from Decisions
- Composio Connect replaces Rube (Preferences → API Keys → Composio)

### Fixes

- Remote sidecar: mouse click position routing
- Oracle skin idle/animation and file-drop glow
- Telegram intercom requests

### Tests

- Added or extended: chat compaction/activity, loop presets, schedule blocks, WhatsApp board linking, orchestrator ticket handoff, workflow steering, remote audio, IDE backends, harness bootstrap, plugin paths, harness packs, MCP harness, IDE threads

---

## [2.7.17] - 2026-06-05

### Orchestration, IDE handoff, Automations

- Orchestrator events for workflow runs, automations, IDE activity, browser evidence, nudges, and ticket-board handoffs share a clearer shape
- Cursor and Codex sessions report project and session context back, including chats not started from a workflow
- Ticket Boards: sending a ticket opens a live agent thread with speech, linked project details, and the correct chat id
- Automations section added: itemized instruction workflows with create, edit, remove, scheduling, Run Now, and per-automation history
- ECC vendored with provenance; skills deduped; setup repairs Codex, Cursor, and Claude wiring
- Codex/Cursor plugin verify/repair during setup; Claude gets the compatible harness surface
- Browser/Playwright runs keep per-session evidence (screenshots, console/network) tied to the starting project, workflow, automation, or IDE chat
- Telegram/desktop responses routed through a central policy: log low-value status, prefer voice when useful, suppress repeated idle nudges, summarize provider errors
- Telegram: inbound messages wait for the agent bridge at startup; outbound rate limits defer instead of drop; remote link requests preserved; scheduled automation reports no longer duplicate into chat/Telegram
- Internal audit/project-execution workflows hidden from the normal workflow list; orphaned IDE bridge sessions cleaned; placeholder project labels replaced
- Memory layer splits conversation history from durable preferences; weekly compaction groundwork
- Ticket Boards UI: loading, Messages/Boards switch, ticket controls, modals, project-linked context

---

## [2.7.16] - 2026-05-30

### More visible agent work, better workflow memory, and cleaner handoffs

Agent work is less of a black box. When DecisionsAI sends work to Codex, Cursor, VS Code, or another coding agent, the workflow can now keep a clearer record of what was sent, which agent picked it up, what happened during the run, what evidence came back, and whether the work passed validation.

Workflows can start learning from repeated feedback. If you keep correcting the same kind of mistake, DecisionsAI can now turn that pattern into a visible rule for future runs. The goal is simple: you should not have to keep repeating the same standards every time a ticket goes through an agent.

The Workflows screen now shows more of the run. Active runs can show the chosen executor, recent events, validation results, correction attempts, approval state, runtime context, and what the system plans to do next. This makes it easier to see whether a workflow is actually moving or waiting for input.

Codex and Cursor/VS Code handoffs are easier to follow. Work sent into Codex can report progress, steering, blockers, completion, and failure back to DecisionsAI. Cursor and VS Code work packets now include clearer workflow metadata, stay waiting by default, and include a command to report when IDE work is complete.

Validation and retry behavior got smarter. When a step fails, DecisionsAI stores what was expected, what was observed, and what needs to change. Retries can use that evidence instead of starting from a vague "try again" instruction.

Tickets can get more useful execution context. The system can now attach relevant skills and guardrails to project work before sending it to an agent, so instructions are less bare and agents have a better chance of doing the right thing on the first pass.

WhatsApp-to-ticket handling is cleaner. Boards can focus on newer unticketed WhatsApp messages, avoid reusing messages that were already turned into tickets, and explain when there is nothing new to process.

Telegram reconnects are quieter. When the relay is offline or the network is flaky, DecisionsAI now logs clearer, less noisy status messages while it keeps retrying in the background.

Settings and model reliability improved. Saving third-party settings is safer around existing secrets, cloud model names are preserved more reliably, and voice/model settings received another stability pass.

More regression tests were added. The release includes coverage for Codex and IDE handoffs, workflow learning, validation, route approval, retry behavior, skill selection, settings safety, Telegram reconnect logging, and the main workflow backbone.

---

## [2.7.15] - 2026-05-13

### Snippets, Codex plugin groundwork, remote control polish, and stronger agentic workflow handoff

Snippets are back as a first-class app feature. The main web UI now has a simpler Snippets area focused on the actual text and optional hotkey, while the remote control can open the shared snippet list from a dedicated round button. Snippets can be added quickly, shown as numbered options, and pasted directly into the active cursor position for fast reusable text anywhere on the desktop.

The DecisionsAI Codex plugin work began. A new Codex-side plugin wrapper was scaffolded so DecisionsAI can hand project, ticket, workflow, and Initiative work into Codex with clearer operating instructions. It includes the first DecisionsAI Codex worker skill, local install notes, backend checks, and the feedback shape needed for Codex progress to flow back into DecisionsAI.

The remote control got a cleaner, more capable control surface. Dictation, agent submission, screenshots, snippets, skills, workflows, and tab actions now have clearer separation and stronger connection handling. The remote app gained Skills/Workflows parity, better screenshot handling, and a smoother launch experience from the website.

TTS, dictation, and spoken instructions are smoother in live use. ElevenLabs and other live TTS providers now get stronger sentence normalization and stream handling for slashes, punctuation, long-form speech, and sentence boundaries. Dictation also understands contextual phrases like "type that out" as an instruction to use the previous assistant response.

Project CLI backends moved closer to a real adapter system. Pi remains the default, while project coding work now has a cleaner backend shape for Cursor CLI, Claude Code, Codex, and future CLIs. The Projects CLI tab was tightened so backend selection, setup state, model availability, and project details share one clearer contract.

Initiative, workflows, and ticket execution are more connected. Initiative settings now have stronger boundaries for proactive behavior, work scanning, Telegram prompts, and workflow handoff. Workflow steps gained better result packets, feedback contracts, Codex-aware execution plumbing, and regression coverage around ticket-to-workflow-to-agent loops, giving the agent a clearer path from noticing work to asking, executing, and reporting back.

Telegram and WhatsApp relay stability improved on the server side. The remote relay moved toward Postgres-backed production storage, runtime artifacts are ignored properly, screenshot/media cleanup is in place, websocket auth paths are restored, and the relay services were rebuilt/redeployed cleanly. The result is a steadier bridge between the desktop app, Telegram, WhatsApp, and the remote web UI.

Regression coverage expanded across the agentic loop. Added and extended tests for TTS sentence streaming, live provider cleaning, dictation context, Telegram delivery stability, project CLI backend contracts, Codex workflow handoff, result packets, workflow feedback, initiative work actions, and remote workflow behavior.

---

## [2.7.14] - 2026-05-10

### Agentic workflow tightening, chat trace polish, hotkeys, dictation, and audit follow-through

A full ecosystem audit was completed across the agentic developer workflow. The audit covered the main orchestrator, Projects, Ticket Boards, Workflows, Skills, Chat, Initiative, Telegram, tool execution, computer-use validation, and audio/TTS behavior. The conclusion was that DecisionsAI already has strong execution primitives, but the next product priority is reliability and cohesion: fewer janky tickets, clearer workflow mapping, better validation, better tool feedback, and less silent failure across the whole loop.

Chat now has a richer activity trace for real work. Tool executions and workflow/sub-agent progress can now appear in the chat timeline as structured activity instead of loose, noisy, out-of-order cards. The web chat consolidates related tool calls into a single activity block, keeps late-arriving tool activity near the assistant turn it belongs to, removes duplicate method/result wording, and handles empty realtime streaming placeholders so blank assistant bubbles disappear instead of lingering until refresh.

Workflow and sub-agent activity is now durable in chat. Workflow runs can record started, waiting, resumed, cancelled, step started, step completed, step failed, and completed events back into the originating chat. That gives long-running workflow work a visible trail instead of making the user infer what happened from a final response or from separate workflow screens.

Ticket creation and board linkage were tightened. Local Decisions tickets now explicitly target `.tickets/` only in `DEBUG=True` development mode, and ticket generation gained better intent parsing, project/workflow context handling, and recommended skills. Local, Trello, and Jira ticket creation paths now share the skill recommendation behavior so remote boards do not fall behind the local board experience.

Skills are now part of the ticket workflow conversation. When a ticket is created, DecisionsAI can include recommended skills that should help execute or review the work. This makes tickets more useful as handoff objects instead of just storing the user's raw request.

Computer-use workflows gained a dedicated path. A new computer-use workflow step type and tighter agent-instruction computer-use mode were added so screen tasks can follow an observe-act loop with screenshot feedback, lower iteration caps, early stopping, and sidecar execution. This moves workflow automation closer to a real desktop-operating loop while keeping validation and stuck-state behavior visible.

Clipboard and typing tools were expanded. The tool layer now includes explicit clipboard-writing support and stronger typing/text-entry plumbing, covering the missing path where the agent needed to place generated text onto the clipboard or type dictated/generated content reliably.

Hotkeys were rebuilt around configurable defaults. Shortcut settings now use a canonical hotkey profile shared by backend validation and the web UI. Defaults are editable in Preferences, and saving shortcut settings now immediately notifies the live Oracle hotkey listener instead of requiring a restart. The current default push-to-talk hold combo is `Option + Command`; hold-to-dictate is enabled by default on `Control + Command`.

Hold-to-dictate was fixed end to end. Dictation hotkeys now use the dynamically configured shortcut, including modifier-only hold combos, switch the Oracle into the dictation visual state immediately on press, reset the Oracle state on release, and route the transcript through a text-entry-only path instead of command, fast-action, workflow, or chat response handling.

The web UI shortcut settings now match the runtime. The Shortcuts settings page and backend validation now share the same option lists and defaults for push-to-talk, Oracle sizing, action recording, web navigation, skin navigation, skin selection, and dictation.

Testing coverage was broadened around the tightened areas. Added or expanded tests for dictation hotkeys, shortcut save signaling, chat tool-event persistence, workflow chat tracing, remote ticket skill recommendations, ticket intent parsing, workflow planning normalization, tool intents, clipboard actions, sidecar computer-control behavior, computer-use step integration, and chat tool merge ordering.

---

## [2.7.13] - 2026-05-05

### Hotfixes + stability

Workflow reliability got a big stabilization pass. Continuation and resume behavior were tightened so runs are less likely to drift, double-process, or stall mid-step. Context handling for longer workflow runs was also hardened so step execution stays more predictable under heavier real usage.

Safety around file operations was upgraded. Risky file actions now go through stricter guardrails and clearer confirmation behavior before destructive operations, reducing the chance of accidental damage during automated or tool-driven workflows.

Initiative and media-path reliability both improved. Initiative health/status and recovery behavior were hardened across backend + settings paths, and WhatsApp/media path resolution was cleaned up so media-related routing fails less often when files are moved, relayed, or resolved through different entry points.

LLM/tool routing and avatar-state behavior were tightened. Tool-path reliability was improved to reduce wrong-path execution and silent odd behavior, and avatar-state debugging now logs full transitions (`idle -> thinking -> idle`) so state issues are easy to trace. This release also fixes a real stuck-thinking bug where pressing push-to-talk again during generation could leave the avatar in `thinking`, with added idle recovery so odd stop/cancel paths reset correctly.

---

## [2.7.12] - 2026-05-03

### Big stability release

Live updates and sidecar reliability both leveled up. The web UI now receives real-time SSE events from `/api/events/stream` instead of relying on polling behavior, and sidecar health/wire-version handling was tightened with cleaner reconnect backoff so mismatch and disconnect failures are less messy.

Workflows got a serious stability and usability pass. Core execution behavior was tightened around continuation/resume flow, step execution safety, and workflow context limits, while the workflow UI got cleaner by moving export/duplicate/download/delete-all actions into a row-level right-click menu where they are easier to use.

Safety and reliability improvements landed across core systems. File-operation guardrails were strengthened with clearer destructive-action confirmation behavior, initiative health/status paths were hardened, media-path resolution for WhatsApp-related flows was improved, and LLM/tool routing reliability was tightened to reduce silent failures and wrong-path execution.

General polish and coverage improved at the same time. Voice/player timing was tuned to reduce the "still talking" feel after TTS, practical fixes were applied across Kanban/chat/settings, MCP and initiative groundwork continued to expand, and test coverage was strengthened across event streams, workflow behavior, file-safety guards, and media-path handling.

---

## [2.7.11] - 2026-04-27

### Workflows, Hotkeys, Desktop Polish

What a workflow is (if you haven't used it yet): In the web app under Workflows you build a **named list of steps**. Each step is an action for the assistant, natural-language instructions, running a saved mouse/keyboard recording, shell commands, browser automation, HTTP calls, and similar. Steps run in order. Some steps can pause until you continue. **Agent Context** is separate text (or items, see below) that gets applied on every step so the model sees your rules, credentials, and conventions without you repeating them per step. Workflows can also be tied into ticket boards and projects so runs show *which board, ticket, and project* they belong to.

What changed in this release

Running and continuing a workflow. Fixed cases where the same step could run twice or "Continue" didn't move the run forward in the UI. Waiting steps are supposed to resume cleanly, and the Workflows page is meant to stay in sync with what the backend is doing. Active run summaries list more useful context (including project) so you're not staring at a run with no idea what it's for.

Agent Context is a manageable list. You can add, edit, and delete multiple context entries (rules, snippets, credentials notes) instead of maintaining one giant text field. Combined with what you already had in 2.7.10, this is the "several sticky notes instead of one wall of text" model.

Building and reading a workflow. Steps can be reordered (drag the **⋮⋮** handle on each row). The list shows clearer run/wait state, and which boards use a workflow. Stop and delete on steps include confirm where it helps. Export / duplicate / download behavior from 2.7.10 stays; this release is mostly execution reliability and editor detail.

Schedule vs ticket board. If something on a **ticket board** starts a workflow at the same instant a **time-based schedule** would start the **same** workflow, the board-triggered run is preferred so you don't get two competing starts for one workflow.

Action recordings. When you stop recording an action, you get a prompt to confirm or type the action name instead of silently saving under a vague default.

Keyboard shortcuts (all configurable). Under **Preferences → Shortcuts** you can turn shortcuts on/off and choose modifiers and keys. This release adds and wires shortcuts for: starting/stopping action recording from the desktop; jumping to Chat, Projects, Actions, Snippets, Workflows, and Preferences in the browser; cycling the Oracle / avatar skins. Defaults use Command+Option combinations on macOS; you're not locked to those if you change them in Preferences.

App exit and voice. Quitting after you confirm is less likely to freeze the window stack. Text-to-speech playback was adjusted so responses don't sound choppy when events fire close together.

VS Code tickets from workflows. If a workflow creates or updates a Cursor/VS Code ticket, you can use **append mode** so new work attaches to the current ticket instead of always opening a fresh one, and workflows can receive a status callback when the ticket path is set up for it.

---

## [2.7.10] - 2026-04-26

### Workflow Editor Cleanup

Workflows UI simplified: Draft/Active/Paused controls were removed, the title area was cleaned up, and the run/reset/delete actions are now the primary controls in the header.

Agent Context unified: Context and variables were consolidated into one **Agent Context** area so global workflow guidance lives in a single place.

Tabs and actions reorganized: Schedule moved to the end of the tab row; export/download/duplicate moved to the selected-workflow footer area in the left panel.

Steps panel cleaned up: "Add Steps from AI" moved below the list, idle steps show less badge noise, and cancelled output is less repetitive.

Step history controls added: You can clear per-step audit history to reset results and run with a clean slate.

Kanban check-in responses improved: Check-in can target one board or all enabled boards, and the response messaging is clearer when nothing runs.

Layout alignment improved: Main nav width now matches full-page content width.

---

## [2.7.9] - 2026-04-22

### WhatsApp Media, Relay, and Kanban Messages

Inbound media reliability improved: WhatsApp images, voice notes, and documents are fetched more reliably instead of showing broken or missing previews.

Relay fallback fixed: If media is not yet local, the desktop now resolves and requests the correct relay item using the right message identity.

Message dedupe tightened: Back-to-back media messages no longer collapse into one bubble and break thumbnails.

Kanban Messages code split: WhatsApp-related UI logic was separated into clearer modules to make behavior easier to maintain.

Media rendering tuned: Image/video bubble sizing is more controlled, and non-previewable files still appear as usable attachment links.

Relay cleanup added: Unreferenced media files are cleaned up to avoid relay storage bloat.

---


## [2.7.8] - 2026-04-17

### WhatsApp Snapshots, Agent Terminals, Real-Time CLI

WhatsApp → Ticket in one click: Snapshot an entire WhatsApp thread into a kanban ticket. Messages get batched together in the Backlog lane with a camera badge showing which ones were included. No more copying messages one by one.

Agent can start your project: The agent now has a "start project" tool that launches startup terminals directly from a chat. Say "start DecisionsAI" and pi boots your dev server, runs your migrations: whatever your startup instructions say.

Real-time CLI feedback: Every agent tool (push ticket, push skill, send instruction) now routes through a single dispatch so the CLI tab shows what's happening as it happens. No more guessing whether the agent actually did something.

Switch models from the CLI: Pick any model right next to the input bar. Ollama and OpenAI models grouped by provider. Change it and pi restarts with your selection instantly.

Pi reads back its work: When pi finishes a task, it summarizes what it did and speaks it aloud. Sent from Telegram? The summary goes back to your chat instead.

Projects ↔ Boards linked both ways: Set a kanban board on a project and that project auto-links back as the board's default. One setting, bidirectional.

Ticket copy includes media: Copying a ticket to clipboard now includes the full media file URLs alongside the text. Paste it anywhere and nothing gets left behind.

Startup aliases work: Aliases like `runserver` and `workon` defined in your `.zshrc` now work in startup terminals. No more writing out full commands.

Terminal overview sees tools: The overview summary now includes tool activity (which tools ran, which failed) alongside the last command and response.

---


## [2.7.7] - 2026-04-16

### Cloud Models, pi Agent, Terminal Overview

Cloud Models by Default: New installs now use `minimax-m2.5:cloud` for chat and `glm-5.1:cloud` for coding. Zero local RAM needed. Your old local model settings get swapped automatically on next launch.

pi Agent: You can now talk to the pi coding agent straight from chat. Ask it to write code, fix bugs, refactor stuff: all without leaving the conversation.

Terminal Overview: The assistant can now glance at your terminal tab and react to what it sees. Build errors, test failures, whatever's on screen.

Step Runner Presets: Six ready-made presets for common tasks: file ops, HTTP health checks, opening apps, Python data pipelines, and web login/scraping with Playwright.

pi CLI Setup: Setup now installs and configures the pi coding agent automatically. One run and both DecisionsAI and pi are ready to go.

---


## [2.7.6] - 2026-04-14

### Sidecar Tools, Workflow Engine, Small Model Tool Calling, Coqui TTS, Initiative System

Sidecar machine tools expanded: Added `screen_analyze`, `run_python`, `drag_to`, `scroll`, and `wait_for_element` for stronger desktop control and screen-aware automation.

Workflow runtime rebuilt: Workflow internals were modularized and step execution became more reliable (tool-based execution, proper async continuation, timeout handling).

Small-model tool calling supported: Models without native function calling can still use tools through intent parsing plus safety guardrails.

LLM settings consolidated: Conversational, coding, vision, image, computer-use, workflow, and kanban model slots are now configured in one place.

TTS options expanded and stabilized: Coqui voice cloning support was added; reliability fixes prevent TTS silence and wrong-provider voice routing.

Initiative system introduced: Added four initiative modes (`observe`, `assist`, `operate`, `own`) with scheduled policy-checked execution cycles.

Autostart support added: App can launch on login via settings.

Legacy Step Runner removed: Remaining legacy Step Runner paths were removed so workflows stay on the unified execution model.

---

## [2.7.0] - 2026-04-09

### Unified Workflows, Chat Setup, Remote Controls, Faster Local Models

Workflows are now one system: Step Runner and Workflows were doing the same thing separately. Now it's just Workflows. Your existing Step Runner sessions migrate over automatically on startup. One place to build, schedule, and run automations.

Chat setup screen: Opening the chat page now gives you a proper setup form: pick your LLM provider, model, voice, and oracle skin before your first message. The "Configure New Chat" modal got the same treatment. Everything loads in the background so you're not staring at empty dropdowns.

Oracle skin picker: Choose your oracle or avatar skin right from the chat setup. The skin picker shows live previews of all installed skins. Skins now support ping-pong playback and looping animations, and you can resize the oracle from the settings.

Free model access: There's a Kilo shortcut in the chat setup that takes you straight to signing up for free cloud models. Step-by-step instructions built in: no docs needed. KiloCode is now the first provider listed in Third Party settings.

Remote web UI controls: Dictation and agent input buttons, window swapping tool, expanded keyboard controls. You can do a lot more from the browser now without touching the desktop app.

Faster local Ollama responses: Optimized how the system prompt is built for local models. Removed duplicate tool info and trimmed unnecessary sections. Model dropdowns now only show Ollama models that actually support tool calling: no more picking a model that can't do anything.

Voice navigation: Tell the agent "open skins", "open audio settings", "open providers" and it navigates directly to that section. Works for every page and settings tab in the app.

Stability & offline use: Speech-to-text (Vosk fallback, Whisper load failures) and fast-action routing hardened. Tailwind bundled locally so the web UI works without relying on a CDN. Workflow schema updates apply on startup.

---

## [2.6.9] - 2026-03-31

### Google Gemini, Kiro CLI Pipeline, Telegram Overhaul, Windows Polish

Google Gemini Support: You can now use Google Gemini as an LLM provider. Add your API key in the providers UI, pick a Gemini or Gemma model, and you're good to go. API errors like quota limits, rate limits, and bad keys now show up clearly in the chat instead of silently failing.

Kiro CLI as an Agent Tool: The orchestrator can now send coding tasks to Kiro CLI directly. When a kanban ticket is linked to a project, the agent routes it to Kiro CLI for execution instead of trying to do it itself. Tickets flow through lanes automatically: from Current to QA/Assess instead of jumping straight to Done: so there's an actual review step. You can create multiple tickets from a single voice command, and the agent logs everything it does in a real-time audit trail that streams to the UI.

Kanban Boards Got Smarter: Boards now link to projects, so the agent knows which codebase a ticket belongs to. Each board has its own agent check-in toggle (the robot icon in the sidebar) and configurable lane routing. You can set how often the agent checks in: down to every few minutes if you want. Trello and Jira boards show up alongside local boards, and you can archive/unarchive boards from the context menu.

Telegram Response Format: Telegram responses now match your input format by default. Send a text message, get text back. Send a voice note, get a voice note back. There's a persistent "text only" mode you can toggle by just telling the agent "respond in text": it remembers across messages and restarts. The old thread-local flag system that was causing responses to randomly disappear has been completely replaced with database-backed settings.

Telegram Tool Results: Previously, when the agent used a tool during a Telegram conversation, you'd sometimes get an empty "Done" message instead of the actual result. Fixed. Tool results now get forwarded properly, and stale screenshot flags get cleared between responses so you don't get yesterday's screenshot attached to today's message.

Windows installer & runtime: Proper shortcuts, uninstaller, single-instance guard, and fewer background-console flashes on launch.

Restart & Exit: The restart and exit tools were rewritten to use the event queue instead of Qt signals, which fixes crashes when they were called from background threads. Restart actually works now: it does a clean `sleep + exec` instead of the old approach that would sometimes just kill the app without coming back. There's a new "Restart" option in the oracle's right-click menu too.

Voice & routing: Voice provider labels normalize consistently; TTS sentence splitting no longer splits on version-like numbers (e.g. `2.5`). LLM providers receive the full tool catalog so the model chooses tools instead of the app hiding them by provider.

---

## [2.6.8] - 2026-03-27

### Security & Windows Installer Fixes

LiteLLM Supply Chain Attack: On March 24, two versions of the LiteLLM package (1.82.7 and 1.82.8) were published to PyPI with hidden malware that steals passwords, cloud credentials, and SSH keys. Both versions have been removed, but we've updated the installer to explicitly block them so they can never be installed: even from a local cache. We already had LiteLLM pinned to a safe range, so no DecisionsAI users were affected, but we added the explicit exclusion as an extra safety measure. If you installed any Python AI project between March 24-25 that uses LiteLLM, check your version. [Full breakdown from The Register](https://theregister.com/2026/03/24/trivy_compromise_litellm).

Python 3.12 Enforced: The installer now checks for and requires Python 3.12 specifically. We already had this pinned, but the installer now actively blocks other versions to avoid compatibility issues with native packages like onnxruntime and pywhispercpp.

Windows DLL Fix: Fixed a crash on Windows where the speech engine (Kokoro) wouldn't load because two conflicting versions of the same library were fighting each other. The installer now sets up the correct one first so there's no conflict.

Dependency Pinning: Pinned Pydantic to avoid a version that requires Rust to compile on Windows (most users don't have Rust installed). Pip is now auto-upgraded to the latest version before installing anything. All installs skip the pip cache to avoid stale packages causing failures, and the installer automatically clears the cache and retries if something goes wrong.

---

## [2.6.7] - 2026-03-27

### Windows Installer, Qwen3-TTS Removal, Bug Fixes

Windows Installer: The Windows installer now handles a lot more automatically: installs Git if it's missing, sets up C++ build tools for compiling native packages, adds DecisionsAI to your system PATH so you can run it from anywhere, and fixes the Windows 260-character path limit that was breaking some installs. If a package can't be compiled, the installer skips it and falls back to an alternative.

Qwen3-TTS Removed: We removed Qwen3-TTS as a voice option. It sounded good in demos, but in real conversations the latency was too high: you'd be waiting a couple seconds for every response. Kokoro (offline, fast) and ElevenLabs (cloud, high quality) are the recommended options for text-to-speech and voice cloning.

Telegram: Voice notes sent through Telegram now use whatever voice you've picked in your chat settings instead of a default. The welcome message gets forwarded to Telegram when you connect. On Mac, the system stays awake while Telegram is connected so the bot doesn't go silent.

Google: You can now disconnect Google with a button (instead of manually deleting files). Connected services show a checkmark so you can see at a glance what's linked.

Voice & Mouse: Fixed "move mouse to center" accidentally taking a screenshot instead. Fixed audio glitches in push-to-talk where blank audio artifacts would show up as text. Fixed the agent randomly dying every 5 minutes due to an idle timeout.

Bulk tickets & config: Multiple tickets from one voice command; unified config location; version shown at startup.

---

## [2.6.6] - 2026-03-25

### Oracle Skins System: Your Assistant, Your Way

This one's huge. Remember swapping Winamp skins back in the day and completely changing how your music player looked and felt? We brought that energy to DecisionsAI. The oracle window now has a full skin system: pick a character, and the entire look and behavior of your assistant changes instantly.

Skin Gallery: The Skins tab in Preferences shows every available skin as a big visual card with a live preview. Click one and it switches instantly. Oracle stays round with the glow ring. Avatars go square and transparent: the character floats on your desktop with no background.

Event Hooks: Each avatar skin maps application events (thinking, listening, working, needs attention) to different animations. When the AI is processing, your avatar switches to its thinking animation. When it needs your input, it switches to the attention pose. All configurable per-hook in the skin editor.

Ping-Pong Playback: WebM and GIF animations can play forward-then-backward in a smooth loop, or just loop normally. It's a per-event setting in the skin config, so idle can ping-pong while other states restart from frame 0.

Chroma-Key Transparency: Avatar WebM files have their background color automatically detected and removed at load time. Each skin specifies its background color in the config, and the system removes it with smooth edge falloff: no harsh cutoffs. The result: characters float transparently on your desktop.

Skin Config Files: Every skin is a `skin.json` in its avatar folder. It defines the rendering shape (round/square), border, shadow, glow behavior, image scale, chroma-key color, and the full event-to-animation mapping. Drop a new folder with a `skin.json` and it appears automatically.

Oracle GIF Picker: The oracle skin has 20 different animated GIF backgrounds. Pick one from the dropdown in the Skins tab and it updates live.

GlowEngine: The glow ring on the oracle is now driven by a dedicated engine with four styles: breathing (sinusoidal), pulse (PTT), fade (InOutCubic for dictation), and flash (file drop success). All configurable per-event in the skin config.

Static Image Skins: Skins can use PNG/JPG/WebP images instead of animations. Hayley uses three PNGs (idle, thinking, processing) that swap based on what the AI is doing.

Context Menu Naming: The right-click menu now says "Hide Clippy" / "Show Clippy" instead of "Hide Oracle": driven by the skin's name field.

Settings Migration: Existing users' oracle settings (GIF filenames like "0.gif") are automatically migrated to the new skin system on first launch. No manual steps needed.

Fixes: PTT stuck after drag; glow clearing on release; animation flicker from overlapping render paths; removed dead legacy oracle code.

---

## [2.6.0] - 2026-03-23

### Codebase Overhaul, Step Runner, Playwright, Telegram Remote Control

Big internal cleanup: the entire project was reorganized, about 15,000 lines of dead code removed, and the folder structure actually makes sense now. Over 200 files were touched. If you're reading the code, it's way easier to navigate.

Step Runner Overhaul: You can now bind actions directly to steps, so a step can trigger a saved macro instead of just sending text. The whole run flow is more intuitive: start, pause, skip, cancel all work the way you'd expect. Pass/fail routing and agent-decided routing are reliable now.

Playwright Browser Tool: The agent can open a headless Chrome browser, run scripts, and actually see the result. It captures a full-page screenshot and all console logs (errors, warnings, failed requests), then sends both to the vision LLM. If a page is blank and the console has a 500, the agent catches that.

Telegram Remote Control: Type "remote" in Telegram and you get an HMAC-encrypted link to a full web UI for your machine. Screenshots stream live, you can view multiple screens, drag and drop with the cursor, upload and download files, even move files between machines. Chats, actions, step runner, snippets: all controllable from your phone. Each session is token-protected with rate limiting and SSRF prevention.

Fixes: Startup crash with agent persona loading; voice personality on custom voices; fewer redundant DB round-trips on load.

---

## [2.5.0] - 2026-03-21

### REST API, Chat Hotswap, Vision Fix

REST API: Control DecisionsAI from scripts, curl, or any HTTP client. Create chats, send messages, run actions, trigger step runner sessions: all at `http://127.0.0.1:8765/api/`. Built-in docs page at `/docs/` with live testing. The agent knows the API too, so you can ask it for the right curl command.

Chat Hotswap: Switching chats no longer restarts the agent. Model and voice swap on the fly, history flushes without reloading the whole pipeline. Way faster.

Better Defaults: Fresh installs ship with qwen3:8b, qwen2.5-coder:7b, qwen3-vl:2b, and Kokoro Heart. No more outdated llama references.

Vision Just Works: Pick a vision model in settings and the app trusts your choice. No more popups or silent swaps.

Bug Fixes: Fixed first-launch crash, push-to-talk dying after chat switch, voice going silent after tool use, tool call text leaking into chat, audio stream crashes, vision not working with some providers, speaker volume not restoring.

---

## [2.4.0] - 2026-03-20

### Voice Cloning, Screen Intelligence, and Audio Improvements

Clone Any Voice: Create custom voices by uploading a short audio clip (WAV, MP3, M4A, or WebM) or recording one directly in the app using the built-in wizard. Pick Male or Female for better results, give it a name, and your cloned voice appears in the dropdown with a ⭐ prefix. Kokoro cloning runs entirely on your machine: no cloud, no limits. ElevenLabs cloning is also supported and tends to retain accents more faithfully. Works in live conversations, chat previews, Telegram voice notes, and saved audio. Create, edit, and delete custom voices from both Settings and the Chat UI without leaving what you're doing.

Screen Intelligence: The vision system has been rebuilt from the ground up into a modular pipeline that handles 20 distinct use cases. Ask the agent to describe your screen, find a button, click an element, read an error, check if a toggle is on, count open tabs, identify the active app, scroll to a section, navigate menus, fill in forms, and more. It layers OCR text detection, OpenCV element recognition, and vision LLM analysis: picking the fastest path for each request. When it moves your mouse to a target, the cursor follows a natural curved path instead of teleporting.

Cleaner Audio Pipeline: Echo cancellation subtracts speaker output from mic input in real time, and an energy-aware gate only lets your voice through when it's clearly louder than the speaker bleed. Interrupts now cut audio immediately with no overlapping or garbled speech. Hands-free mode is dramatically more reliable, especially on laptops.

Better Pronunciation: Common English contractions (I'm, don't, you're, etc.) are now expanded before reaching the speech engine, fixing cases where words like "I'm" sounded like "imm."

---

## [2.3.1] - 2026-03-18

### Custom Voice Cloning, Windows Support, and Fixes

Custom Voice Cloning: Clone voices with ElevenLabs and Qwen3-TTS. Upload audio clips in Settings, transcription is auto-filled via Whisper, and the cloned voice appears in the voice dropdown with a ⭐ prefix. ElevenLabs uses Instant Voice Cloning (IVC) with a 5-voice limit. Qwen3-TTS clones at inference time using reference audio: no limit. Delete custom voices from the dropdown with the trash icon. Custom voices work in both the web preview player and the live agent pipeline.

Dynamic TTS Provider Registry: Voice providers and their voices are now served from a single registry (`TTS_PROVIDERS` in constants). The UI populates dropdowns dynamically from `/api/tts/providers`: nothing hardcoded in JS or HTML.

Windows Support: Added `decisions.bat` and `decisions.ps1` launchers for Windows. Same auto-setup flow as macOS: checks Python 3.12, creates venv, installs deps, downloads models, and starts the app.

Qwen3-TTS Fixes: Forced CPU device on Apple Silicon (MPS crashes on grouped-query attention). Float32 dtype for non-CUDA. Model downloads to local `models/qwen3-tts/` directory instead of HF cache. Suppressed flash-attn warnings and HF progress bars. Hot-swap now updates voice in-place without reloading the 600M parameter model.

Settings UI: Settings gear icon in the header now shows active state. Removed Replicate from third-party API section. Coqui TTS commented out (no Python 3.12 support).

---

## [2.3.0] - 2026-03-18

### Step Runner Overhaul, Qwen3-TTS, and Chat Fixes

Step Runner: Big upgrade. Steps now show real status (running, done, failed) instead of marking everything done right away. You can cancel a run or skip a stuck step. The agent knows which step it's on and what the goal is. Steps that hang for 5 minutes get marked failed and the run continues. Missed scheduled runs? They'll run when you start the app.

Qwen3-TTS: New local voice option. Install the package, pick it in Settings, and choose from voices like Aiden, Ryan, Vivian, Emma. Runs on your computer.

Chat & Voice: Chat switching is more stable. Single-step runs show real agent replies instead of "Instruction sent." Failed runs are marked failed, not completed. Push-to-talk and TTS timing fixed. When the agent can't find something on screen, it asks you to bring it into view instead of guessing.

---

## [2.2.0] - 2026-02-08

### Browser-Based UI and Code Cleanup

Everything now lives in browser tabs: Settings, Actions, Snippets, and Projects. No more separate windows. Switch between them instantly.

The codebase got a major cleanup. Tools and utilities are organized better, so it's easier to understand and work with.

Playwright Investigation: Started exploring headless browser automation with Playwright for automated testing workflows. Plan, execute, validate, repeat.

OpenRouter: Unified LLM API access through OpenRouter, giving you one key for dozens of models from different providers.

---

## [2.1.9] - 2026-01-29

### Vision Support for All Providers

You can now send images to any LLM provider: Anthropic, OpenRouter, Groq, KiloCode, and Ollama. If the model supports vision, it works. Images are auto-compressed to WebP to save bandwidth and cost. All the existing vision tools (screenshots, image analysis, etc.) work with every provider.

---

## [2.1.8] - 2026-01-26

### Dependency Updates and Bug Fixes

Dependency Refresh: Updated pipecat-ai, langchain, litellm, mcp, sqlalchemy, llama-index, and elevenlabs to their latest versions. PyTorch import is now optional in actions.py to avoid compatibility issues on macOS with Python 3.12.4.

Bug Fixes: Fixed signal_manager import path, database migration errors, and improved error handling for optional dependencies.

---

## [2.1.7] - 2026-01-19

### Project Management and Voice-Controlled Switching

Voice-Controlled Projects: Say "Open project Tensology and start it" and the agent switches to that project, opens it in Cursor or VS Code, and generates startup files. Fuzzy matching handles speech-to-text typos, so "Tensorlogy" still finds "Tensology." After opening a project, the agent asks what you'd like to do rather than guessing.

Duplicate Instance Detection: The app now detects and terminates multiple running instances automatically, preventing resource conflicts.

UI Improvements: Better project management layout with context items and file associations. The ticket board got drag-and-drop improvements, better status management, and cleaner visuals. Audio device hot-swapping works more reliably with automatic fallback.

Fixes: Python 3.10 compatibility restored. Kokoro TTS requirements updated. Fixed a bug where the wrong project would activate due to empty tool parameters. Setup script improvements.

---

## [2.1.6] - 2026-01-13

### STT Streaming Updates

AssemblyAI: Migrated to the v3 streaming API for hands-free mode, fixing deprecated model errors.

OpenAI Whisper: Added Realtime API support for hands-free streaming with batch API fallback for push-to-talk.

Hot Reload: Changing your STT, TTS, or LLM model now triggers a full agent reload so the new config actually takes effect. All STT services display the active model name in transcription output.

---

## [2.1.5] - 2026-01-03

### Telegram Remote Control Improvements

WebP Compression: Screenshots sent to Telegram are now converted to WebP at 80% quality, cutting file sizes by 25-35%. Vision LLM images get the same treatment for faster processing.

More Remote Commands: Double-click support, keyboard shortcuts (select all, copy, paste), extra navigation keys (up, down, enter, page up, page down, break), and a new "instruction" command that sends text directly to the agent as if it came from Telegram.

Quieter Logs: Connection polling and routine status updates no longer spam the logs. Auto-reconnects don't send "online" messages to Telegram anymore, so you won't get notification spam during brief network hiccups.

Stability: Fixed BrokenPipeError and ConnectionRefusedError during shutdown. Added missing stop() method to ActionPlaybackService. Better WebSocket idle handling, message queuing, and reconnection logic.

---

## [2.1.4] - 2025-12-27

### Telegram Integration and File Safety

Telegram Bot: Connect your Telegram account and control the agent with voice messages or text commands. Say "remote control" or "remote" in Telegram to open a web-based interface for navigating your computer screens over WebSocket.

File Operation Safeguards: Every file operation now has confirmation dialogues and safety gates to prevent accidental data loss or unauthorized modifications.

Direct Type Command: Say "type 'hello world'" for immediate keyboard input without LLM processing. Also supports "type from clipboard."

Other Additions: FLAC and other audio format conversions. Cursor ticket creation for development integration. Improved tooling clarity so commands route to the right place.

---

## [2.1.2] - 2025-12-21

### Google Workspace Integration

Full Google Suite: Direct integration with Google Calendar, Docs, Drive, Sheets, and Gmail through OAuth 2.0. Create events, check your schedule, create documents from markdown, list and upload files, read PDFs, check your inbox, send emails, create drafts, reply, and delete. Filter emails by type (inbox, sent, drafts, starred, important, unread, trash, spam).

Smart Routing: When Google is connected, "email" always means Gmail. Google Workspace takes priority for all Google services. Real-time connection testing with streaming results and automatic API enablement reminders.

Fixes: Draft email creation works reliably now. Inbox shows all emails by default (not just unread). Fixed system prompt formatting errors that prevented startup. Better token storage and validation for OAuth.

---

## [2.1.1] - 2025-12-20

About Window: New tabbed interface with a built-in Changelog viewer. Improved emoji support and text replacements. Cleaner styling and layout. Fixed nested scrollbars in the changelog display.

---

## [2.1.0] - 2025-12-10

### OpenRouter and Anthropic Support

OpenRouter: Unified LLM API access through OpenRouter, giving you one key for dozens of models from different providers.

Anthropic Claude: Direct Claude API support. Improved vision capabilities across multiple providers. Improved chat interface with model selection. Better error handling for API connections.

---

## [2.0.0] - 2025-11-25

### Complete UI Overhaul and Tool Ecosystem

New Interface: Complete redesign with a modern dark theme. The actions system was rebuilt from scratch for reliability, and the tray menu now reflects recording state and playback status.

40+ Tools: Full tool ecosystem covering file operations, document processing, audio transcription with speaker diarization, image generation, snippet management, and Cursor ticket creation.

Performance: Improved streaming performance, better memory management, and an improved tool loading system.

---

## [1.5.0] - 2025-10-28

### Web Search, Vision, and Code Execution

Web Search: The agent can now search the web and bring back results.

Vision Tools: Screenshot analysis with vision models, plus a dedicated vision analyzer for image understanding and an image generator.

Document Tools: PDF page extraction and document text extraction for working with files hands-free.

Code Execution: Run Python code and scripts directly through the agent. New system information tool and type-text tool for clipboard input.

Stability: Better error handling across the board. Fixed memory leaks in long-running sessions.

---

## [1.4.0] - 2025-09-20

### Multi-Provider Voice and LLM Support

Speech-to-Text: AssemblyAI integration with real-time streaming transcription and speaker diarization. OpenAI Whisper STT support added as an alternative.

Text-to-Speech: ElevenLabs and OpenAI TTS integration. Multiple voice options across providers.

LLM Switching: Switch between LLM providers on the fly. Chat history now persists between sessions. Model hot-reloading means you don't have to restart the app to change models.

---

## [1.3.0] - 2025-08-15

### Expanded Tool Set

File and Document Tools: Fast file management, document extraction, and audio transcription tools.

Clipboard and Snippets: Clipboard actions, rework and summarize clipboard content, create and use reusable snippets, and save audio from conversations.

Oracle Globe: New globe control tool for the visual interface. New chat and clear chat tools for managing conversations.

Fixes: Clipboard operations no longer block the UI. TTS feedback timing improved.

---

## [1.2.0] - 2025-07-22

### Chat Window and Input Tools

Chat Interface: Full conversation history with streaming response display. Model selector with provider switching. Message action buttons for copy and audio playback. Built-in chat search.

Input Tools: Mouse movement and click tools, caret movement, text editing, media controls, and function/special key support. Redesigned chat interface with better conversation flow.

---

## [1.1.0] - 2025-06-25

### Whisper.cpp and Dictation Mode

Whisper.cpp: Replaced Vosk with Whisper.cpp as the primary STT engine. Real-time streaming recognition with significantly better speed and accuracy.

Dictation and Transcription: Dictation mode for voice typing and transcription mode for clipboard capture. Improved voice activity detection.

Audio Controls: Playback controls, save text as audio, and navigation tools (open window, shortcuts). Push-to-talk and continuous mode switching. Fixed audio playback speed issues and duration calculations.

---

## [1.0.0] - 2025-05-15

### Major Rebuild with Pipecat

New Architecture: Complete rebuild using the Pipecat framework for real-time voice AI. Frame-based architecture with 40-50% memory reduction and significantly improved latency.

Voice Modes: Hands-free continuous listening and push-to-talk. Real-time streaming responses with interruption handling.

Providers: Vosk STT, Kokoro and ElevenLabs TTS, Ollama and OpenAI LLM support.

Controls: Oracle/Globe visual interface, voice commands, mouse and keyboard control, window management, media controls, and basic action recording.

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

DecisionsAI is a voice-first assistant that can also type, click, run workflows, and talk to your coding tools. Less "answer my question," more "help me get something done on this machine."

You can dictate, drive the mouse and keyboard, run multi-step workflows, hand tickets to Cursor or Codex, and get screenshots or test output back on a board. Local models (Ollama) work offline if you set them up that way.

Say "what can you do?" in chat for a live list. Settings is where models, voice, and API keys live. The chat window keeps history if you prefer typing.

---

*For more information, visit [tensology.com](https://www.tensology.com) or [decisionsai.net](https://www.decisionsai.net)*

---

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
