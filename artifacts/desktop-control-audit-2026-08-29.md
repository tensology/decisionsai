# DecisionsAI macOS Desktop Control Audit

Date: 2026-08-29

Scope: Window control, app launch and focus, multi-display placement, Spaces, Finder folders, mouse targeting, accessibility tree, screen capture, model selection, routing, Sidecar permissions, and the macOS Privacy & Security crash reported by Paul.

## Executive conclusion

Desktop control is not failing for one reason. It is failing across four layers:

1. The running Sidecar has invalid macOS TCC identity state. Accessibility was granted to an earlier ad-hoc build, but the current binary has a different code hash. Screen Recording is not available.
2. The Sidecar health check falsely reports Accessibility as healthy because it only runs `cliclick p`, which reads the pointer but does not prove UI control.
3. Natural-language routing does not deterministically select the desktop tools for the commands Paul actually uses.
4. Several requested operations do not exist as proper OS verbs, or exist only as fragile focused-window keyboard shortcuts.

The configured model is a contributor for visual coordinate work, but it is not the primary cause. The system currently fails before the vision model can see a screenshot or before the agent can reach the correct tool.

## Live environment

- macOS 26.5.2, build 25F84, Apple silicon.
- Apple Software Update currently offers macOS 26.6.2.
- Three displays are connected:
  - Main Dell: 1920 x 1080 at origin 0,0.
  - Built-in display: 1512 x 982 at origin 1920,-183, scale 2.
  - LG display: 1920 x 1080 at origin -1920,-158.
- Sidecar is running at `127.0.0.1:11435`, PID 22522.
- Conversational model: OpenAI `gpt-5.2`.
- Vision model: OpenAI `gpt-4o`.
- Computer Use model: OpenAI `gpt-4o`.

## Live endpoint results

| Capability | Live result | Interpretation |
|---|---|---|
| Sidecar health | HTTP 200 | Process is running, not proof that UI control works. |
| Display enumeration | Pass | `get_screen_info` returned all three displays and correct negative offsets. |
| Window enumeration | Partial pass | Visible windows and foreground PID were returned, but titles were empty in the Sidecar result. |
| Cursor position | Pass | `get_cursor_pos` returned coordinates. |
| Accessibility tree | Fail | `get_window_tree` returned HTTP 500 for both frontmost and named apps. |
| Screen capture | Fail | `capture_screen` returned `screenshot failed: exit status 1`. |
| App launch | False-positive success | TextEdit and Calculator returned success but did not produce visible foreground windows in `list_windows`. Processes launched in the background. |
| Visual computer-use loop | Blocked | It cannot start because screenshot capture fails. |

## TCC and permission findings

The Sidecar health endpoint reported:

- Accessibility: `ok: true`, via `cliclick`.
- Automation: `ok: true`.
- Screen Recording: `ok: false`.

This Accessibility result is incorrect. The macOS TCC log records:

`Failed to match existing code requirement ... kTCCServiceAccessibility`

It also shows two different cdhash values for the Sidecar. The Sidecar is ad-hoc signed and identified as `a.out` by TCC. Rebuilding or re-signing it changes the code hash and invalidates the previous Accessibility grant. This explains why permissions can appear to have been granted and then silently stop working after a development rebuild.

The health probe in `sidecar/permissions_darwin.go` only checks whether `cliclick p` returns an `x,y` string. Pointer reading is not a valid test of Accessibility control. It should execute a non-mutating AX trust check in the Sidecar process and separately verify Automation and Screen Recording.

## Privacy & Security crash

The screenshot is a genuine macOS failure:

`Extension process Privacy & Security exited.`

Three crash reports exist:

- 2026-08-25 14:51
- 2026-08-25 15:07
- 2026-08-29 10:44

All three crash at the same offsets inside Apple's signed `SecurityPrivacyExtension` with `EXC_BREAKPOINT / SIGTRAP` on the main thread. The extension signature verifies correctly. This is not a DecisionsAI dialog and not evidence that the Sidecar itself crashed. It prevents the normal UI path for repairing Screen Recording and Accessibility permissions.

Recommended repair order:

1. Back up the Mac.
2. Install macOS 26.6.2, which is already offered by Software Update, then restart.
3. Open Privacy & Security again before changing any TCC records.
4. If it still crashes, test in Safe Mode.
5. If it also crashes in Safe Mode, run Disk Utility First Aid from macOS Recovery and contact Apple with the three `SecurityPrivacyExtension` crash reports.
6. Do not reset the entire TCC database as a first step. That would remove many unrelated permissions and would not repair a crashing Apple settings extension.

## Command capability matrix

| User command | Current status | Why |
|---|---|---|
| Minimize this window | Unreliable | Implemented as a global Cmd+M shortcut on the assumed focused window. No deterministic route and focal cache can be stale. |
| Maximize this window | Incorrect semantics | The focused-window tool uses Ctrl+Cmd+F, which toggles fullscreen, not ordinary maximize. |
| Maximize Codex | Partial but unreliable | Named `set_window_bounds(..., snap="maximize")` exists, but routing is not forced and Accessibility is invalid. |
| Minimize Codex | Missing named verb | There is no named-window minimize operation. Only the global focused-window shortcut exists. |
| Bring up Spotify | Partial | App launch exists, but success is reported without verifying foreground state or a visible window. |
| Move Terminal to left screen | Missing as described | `snap="left"` means the left half of the primary display, not the left physical display. |
| Move Terminal to center screen | Missing | There is no center-display or center-placement snap. |
| Move Terminal to screen 2 | Fragile duplicate path | An older focused-window tool uses an in-process screen cache. The named Sidecar bounds tool has no screen parameter. |
| Move Terminal to second desktop | Missing | No Mission Control or macOS Space API/tool is implemented. |
| Open second desktop | Missing | No Space switching tool is implemented. |
| Open Downloads folder | Implemented but not forced | `SmartOpenTool` has explicit known-folder support, but the deterministic router returns no tool for this phrase. |
| Move mouse to an object | Blocked | Accessibility targeting fails and screenshot-based vision cannot capture the screen. |
| Move mouse to coordinates | Partial | Cursor position works and coordinate movement exists, but the Accessibility health result is false and grants are unstable. |

## Routing audit

The deterministic router returned no tool for all of these exact commands:

- `minimize this window`
- `maximize this window`
- `maximize Codex`
- `minimize Codex`
- `bring up Spotify`
- `move the terminal to the left screen`
- `move the terminal to the center screen`
- `move the terminal to screen 2`
- `open my second desktop`
- `open my Downloads folder`
- `move the mouse to the Spotify play button`

`move the terminal to my second desktop` was incorrectly routed to `file_operations` because the file rule matches the word `desktop`.

The routing property test also fails on the query `"0"` because the TF-IDF fallback mishandles a sparse matrix. This is separate from the desktop commands, but it confirms the retrieval layer has an unhandled failure path.

## Focal-point awareness

The persisted desktop cache said WhatsApp was frontmost while a live Sidecar window query reported Brave Browser. The cache is allowed to remain fresh for five minutes and the AX enrichment call is capped at 0.35 seconds. This is too stale and too short for commands such as `this window`, especially when focus changes as the user opens the Oracle, Chat, or System Settings.

Correct behavior should resolve the target at action time:

1. For `this window`, query the frontmost external application immediately before execution.
2. Exclude DecisionsAI overlays and the Oracle when resolving the user's focal target.
3. Freeze the resolved PID and window ID for the command.
4. Execute the requested verb against that exact window.
5. Verify the resulting state using window-server data, not a success string.

## Model assessment

`gpt-4o` is configured for both Vision and Computer Use. It can analyze screenshots, but in this application it is being used through a prompted screenshot-coordinate path rather than a native OS-control model. It is not the right place to solve deterministic window operations.

Model guidance:

- Do not use vision for launch, focus, minimize, maximize, move, resize, display placement, folder opening, or Space switching.
- Use native macOS APIs and Accessibility for those deterministic verbs.
- Use a current vision-capable model only when an object cannot be found in the accessibility tree.
- Always verify the requested model supports image input and coordinate output before saving it as the Computer Use model.
- Keep vision as a fallback, not the primary window manager.

Changing the model now would not fix the live failure because screenshot capture is denied and the AX endpoint fails before model invocation.

## Test-quality assessment

Focused Python tests reported 77 passing. Those tests mostly mock `_call_sidecar` and assert that expected dictionaries and strings are passed around. They do not test:

- A real macOS TCC grant.
- Re-signing or rebuilding the Sidecar.
- Accessibility-tree traversal from the Sidecar process.
- Screen Recording.
- Three-display negative coordinate geometry.
- Named minimize.
- Ordinary maximize versus fullscreen.
- Space switching.
- Launch-to-visible-window verification.
- End-to-end natural-language routing for Paul's phrases.

The Go Sidecar has no Go tests.

## Prioritized product repair plan

### P0: Make permissions truthful and stable

1. Ship the Sidecar inside the signed DecisionsAI app bundle with a stable bundle identifier and designated requirement.
2. Stop ad-hoc re-signing the executable on every startup or rebuild.
3. Replace the `cliclick p` Accessibility probe with a real `AXIsProcessTrustedWithOptions` check from the responsible signed process.
4. Add one health action that performs a shallow, non-mutating AX query and returns the exact TCC error.
5. Surface Screen Recording, Accessibility, and Automation as three separate status rows with executable path, code identity, last verification time, and repair instructions.

### P0: Repair deterministic routing

Add forced rules for:

- `window_management`
- `list_windows`
- `focus_window`
- `set_window_bounds`
- `launch_app`
- `smart_open`
- `mouse_movement`
- `get_window_tree`
- `screenshot_analyzer`

Disambiguate `desktop` as a macOS Space or display context before applying the file-operation rule.

### P1: Replace focused keyboard shortcuts with named window verbs

Create a single window-control contract:

- Target: frontmost external window, PID plus window ID, app name, or title.
- Actions: focus, minimize, restore, maximize, fullscreen, close, hide, move, resize, center, snap left, snap right.
- Display target: primary, left, center, right, built-in, external, or numeric index.
- Verification: report actual bounds, minimized state, fullscreen state, active Space, display ID, and foreground state.

### P1: Add display and Space semantics

- Enumerate `NSScreen` once and keep stable display IDs plus human labels.
- Map natural words such as left, center, right, laptop, Dell, and LG to display geometry.
- Add Space listing and switching as a separate capability. Do not overload the word `desktop` as a folder.
- Treat display movement and Space movement as different operations.

### P1: Fix focal awareness

- Refresh frontmost context on every deictic command: `this`, `here`, `current`, `what I am on`.
- Reduce or eliminate the five-minute cache for action execution.
- Preserve the last external app only across the brief focus steal caused by DecisionsAI itself.

### P2: Improve app and folder opening

- After launch, wait for the process and at least one visible window.
- Activate an already-running app and unminimize its last window.
- Verify Finder opened the requested folder path.
- Return failure when the requested app has no visible or activated window.

### P2: Add real end-to-end tests

Build a macOS test harness with a signed fixture app and a disposable window. Test the exact user phrases, three-display geometry, rebuild identity behavior, TCC denial, wrong focal target, minimize, maximize, fullscreen, app launch, Downloads, mouse-to-element, and recovery after a failed screenshot.

## Final diagnosis

The main root cause today is not the selected vision model. The immediate blockers are a stale TCC grant caused by Sidecar code-identity changes, denied Screen Recording, a crashing Apple Privacy & Security extension that prevents easy repair, missing deterministic routes, and incomplete OS window verbs. The vision model should be revisited only after those layers are repaired and verified.

## Implementation outcome

Completed on 2026-08-29:

- TCC-sensitive desktop operations now run in the signed Decisions process first. The optional Sidecar no longer needs its own Screen Recording or Accessibility grant for window, screenshot, or UI-element work.
- Sidecar health now reports its executable, identifier, build hash, signature detail, and whether its identity is stable. Ad-hoc development builds are explicitly reported as unstable.
- Window targeting resolves the live foreground window at execution time when no app is named. Named app, PID, and title targeting remain available.
- Minimize, restore, maximize, fullscreen, close, hide, focus, center, left/right snap, explicit bounds, and display moves use explicit window APIs rather than global shortcuts.
- All connected displays are enumerated with correct top-left global coordinates, including negative X offsets and per-display scale.
- macOS Spaces can be listed, switched, and used as window destinations independently from display movement and the Desktop folder.
- App launch waits for NSWorkspace verification and activation. Finder folder opening verifies the front window target path.
- Accessibility-tree mouse targeting has an in-process implementation, and matching requests also force the screenshot analyzer as a vision fallback.
- Exact deterministic routes were added for the audit phrases, including `this window`, named Codex and Terminal targets, left/second displays, numbered desktops/Spaces, Spotify, Downloads, and mouse-to-object requests.
- The sparse TF-IDF out-of-vocabulary crash found during the audit was repaired.

Verification evidence:

- 38 focused routing, window-management, and retrieval tests passed.
- 95 related desktop-control tests passed, with 12 opt-in platform tests deselected by the default suite.
- 2 terminal workspace Playwright tests passed after integrating its Back, Refresh, Stop all, and Start all controls into the top breadcrumb.
- Live checks passed on three displays for app launch, focus, minimize, restore, maximize, fullscreen, cross-display movement, Finder Downloads verification, accessibility-tree mouse movement, Space listing, Space switching, and moving a disposable window to a Space.
- Decisions reported `all_ok=true` and `setup_needed=false` for its signed-process Accessibility, Screen Recording, and Automation permissions after restart.
