# Handoff

_No handoff recorded yet._

_updated: 2026-07-12T10:44:02Z_

## 2026-07-14 — WhatsApp QR relay reset

User reported the DecisionsAI Third Party Vendors → WhatsApp screen waiting forever for a QR code. Local app on `127.0.0.1:8765` proxied `/api/advanced/whatsapp/qr` to the remote relay and returned `status=disconnected` with no QR. Reset the remote WhatsApp relay session via its authenticated disconnect endpoint; subsequent relay and local proxy checks returned `status=qr_ready` with QR payload/image. User should click Refresh/reload the WhatsApp settings card if UI still shows the stale waiting state.

## 2026-07-14 — Remote push-to-talk latency and interruption

Traced production remote voice traffic across `www.decisionsai.net` and the local DecisionsAI client. Implemented a smaller mono 16 kHz / 24 kbps capture path with native browser base64 conversion, fail-fast disconnected sends, visible processing/error feedback, and immediate release of the PTT button after transcription. Fixed pending-audio interruption losing its request ID. Added `remote_agent_interrupt` so pressing PTT during an active turn cancels obsolete LLM/TTS work and clears only that request context before recording the new turn. Added a checked-by-default **Press Enter** option to both Agent and Dictate text modals; Dictate applies it during insertion and Agent applies it after the completed turn. Website commit `41284c6` is pushed and deployed to production; desktop commit `7d36aa7` is pushed to GitHub. Production relay is active and `/health` returns OK. Verification: remote-app build/audio tests and targeted ESLint passed; 47 remote voice/reply/TTS Python tests passed.

Replaced the remote app's older/glitchier `public/voice.gif` with the exact `DecisionsAI/assets/img/voice.gif`. Website commit `23b815d` is pushed and deployed; source, built production asset, and public HTTPS response all match SHA-256 `3dcb6d9f5df08839797b6a16bad0ff65e4bd0feabb100627e780f54b7998acb7`.

## 2026-07-14 — Remote fast-action audio routing audit

Confirmed from local logs that the 22:11 SAST remote Codex-visibility question entered the `developer_context` fast path and played through MacBook Pro Speakers. Audited all fast-action response handlers and routed screenshot results, clipboard rework, action playback errors, generic completion acknowledgements, developer-context answers, and direct web-search results through the remote-aware delivery helper. Successful action playback now acknowledges remote callers instead of returning nothing. Also fixed an undefined `tool` reference in clipboard rework. Added focused remote-routing regression tests; 23 targeted tests pass and Python compilation succeeds. Changes are local and uncommitted amid the user's existing dirty worktree.

## 2026-07-14 — Voice-worker boot profiling and lazy service loading

Profiled the 23:13 SAST launch. Whisper construction was only ~165 ms and warm-up 78 ms; the dominant delay was Python import topology. `agent_worker` imported the full GUI `distr.app.main` only to configure logging, and the service packages eagerly imported every STT/TTS/LLM provider. Added a lightweight worker logger, lazy package exports, selected-provider loading in `service_factory`, and descriptor-only TTS registry discovery. Warm import measurements improved `AgentSession` from 8.87 s to 2.90 s and the worker+GUI import path from ~10.9 s to 3.58 s; inactive Kokoro/OpenAI provider modules are no longer loaded while preparing Whisper. Verification: 52 provider/import/routing tests passed, 8 optional-provider tests skipped, compilation and diff checks passed. Changes are local and uncommitted; the running app has not been restarted for a full cold-launch measurement.

## 2026-07-15 — Workflow intake scope, cold-start responsiveness, and human-path QA

Hardened channel-neutral work routing so explicit Telegram/web requests resolve a named project before any ambient `in_use` board, validate workflow availability before ticket creation, preserve media references, deduplicate provider message retries, emit channel activity, and dispatch a fully scoped ticket/project/worktree contract. `dispatch_async=True` now moves WorkflowAgent/model/tool initialization off the caller/UI thread, exposes an `initializing` phase in existing Runs/chat activity, prevents duplicate starts during initialization, and handles cancellation without resurrecting the run. Added focused DB/integration coverage and stabilized the threaded queue E2E on file-backed SQLite. Fixed workflow-board badge overflow that clipped the starts of ticket titles and added a Playwright geometry assertion. Verification: 242/243 broad workflow tests passed before resolving one stale conflicting assertion; the affected 21-test rerun then passed, the canonical Chromium desktop+mobile workflow journey passed (2 tests, 151.09 s), the post-fix desktop journey passed (74.83 s), and Fallow completed with a repository-wide `fail` verdict driven mostly by the pre-existing 130-file dirty tree (17 dead-code issues, 490 complexity findings, one clone group). Changes remain local and uncommitted.

## 2026-07-15 — Workflow operational hardening and Telegram durable identity

Completed the existing Tickets → Workflow/Loop → Runs/chat-feed path without adding an Intake page. Telegram text, voice transcription, photos/documents, captions, and multi-message bursts now preserve a stable source message identity through batching and ticket intake, so relay retries after an app restart cannot duplicate tickets or workflow runs. Existing durable workflow interactions continue to support typed, inline-button, and voice approve/reject/continue/stop/feedback replies. Fixed workflow preflight resolving a database session provider at import time, which could validate against a different context than the active dispatcher in sequential or embedded runs. Refactored changed workflow JavaScript hotspots and removed two unused duplicate E2E scripts. Backend restarts now reuse a private persistent local API credential (`~/.decisionsai/internal_api_token`, mode `0600`), preventing open tabs from entering endless 401/403 SSE/WebSocket reconnect loops after idle/restart; tabs created before this migration need one refresh.

Verification: 410/410 ordered workflow regressions passed; 82 focused Telegram/intake/interaction/model-routing/memory tests passed; a final 73-test integration rerun passed; canonical Chromium desktop+mobile ticket-loop Playwright passed 2/2 in 148.36 s with visible executor/model/skills/tools/context and RED → fix → GREEN state; two real server restarts retained the same credential and health returned in ~6 ms. Fallow verdict is `pass` with zero introduced dead code, complexity, or duplication (13 dead-code and 486 complexity findings remain inherited). Server is running on `127.0.0.1:8765`. Changes remain local and uncommitted in the authorized dirty tree.
## 2026-07-15 production gate continuation

- Supported runtime is Python 3.12.8–3.12.x; `.python-version` pins the verified 3.12.13 environment and `scripts/verify_runtime.py` checks the critical stack.
- Default collection is clean: 2,832 collected / 71 intentional opt-outs. Full default suite passes: 2,804 passed, 27 skipped, 71 deselected, 1 expected failure, 0 failures (433.83s).
- Fixed the three-ticket sequential acceptance race by using file-backed SQLite and waiting for the declared completion count. Ideation → three development tickets → polish passes.
- Fixed lost `loop_started` events: separate-session event writes now occur after the run creation transaction commits.
- Added `.github/workflows/release-gate.yml` and `docs/RELEASE_CHECKLIST.md`; CI gates runtime, collection, default tests, multi-ticket workflow chain, canonical Chromium desktop/mobile journey, compile integrity, and Fallow.
- Fallow changed-code verdict: pass; 0 introduced dead code, complexity, or duplication findings.
- Harness bootstrap now writes community-pack state, projects reference skills, reconciles Codex after plugin/hook resets, and the doctor uses real ECC/competition paths. Doctor is 23/27 ready; only Cursor CLI authentication and optional Cline installation remain partial.
- Browser Use 0.13.x hard-pins Pillow 12 and breaks Pipecat 0.0.100. The supported pair is now pinned and verified: browser-use 0.11.13 + Pillow 11.3 (`>=11.2.1,<12`). Focused Browser Use/TTS tests pass.
- Next release work: live Codex + Pi provider execution against the disposable Spotify/Pizza fixture, Telegram/desktop real-channel proof, then resilience/migration/backup/packaging gates. Do not mark the production goal complete before those are evidenced.
