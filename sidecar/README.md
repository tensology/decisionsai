# DecisionsAI Sidecar

The sidecar is a small Go binary that runs on your machine (Mac or Windows) and gives the DecisionsAI agent full control over it.

## What it does

Connects to the relay server via WebSocket and executes tool calls:

| Tool | What it does |
|------|-------------|
| `run_command` | Run shell commands (bash/cmd) |
| `read_file` / `write_file` / `list_directory` | File system access |
| `capture_screen` | Take a screenshot |
| `screen_analyze` | Screenshot + AI vision analysis (describe/locate/verify) |
| `run_python` | Execute arbitrary Python scripts with optional pip install |
| `get_window_tree` | Walk the accessibility tree of the frontmost window |
| `click_element` | Click a UI element by ID |
| `type_text` | Type text (optionally into a specific element) |
| `press_keys` | Press keyboard shortcuts (e.g. `cmd,s`, `ctrl,z`) |
| `drag_to` | Drag from one position/element to another |
| `scroll` | Scroll up/down/left/right at current position or coordinates |
| `wait_for_element` | Poll until a UI element appears (with timeout) |
| `list_windows` | List all visible windows |
| `launch_app` | Launch an application |
| `focus_window` | Bring a window to the foreground |
| `find_element` | Search for UI elements by name/type |
| `move_mouse` | Move mouse to element or coordinates without clicking |
| `get_clipboard` / `set_clipboard` | Clipboard access |
| `get_system_info` | OS, hostname, CPU info |

## macOS requirements

- macOS 12+ (Monterey or later)
- Grant **Accessibility** permission in System Settings → Privacy & Security → Accessibility
- Grant **Screen Recording** permission for screenshots
- Optional: `brew install cliclick` for more reliable mouse clicks

## Windows requirements

- Windows 10 or 11
- No extra dependencies — uses built-in UIAutomation, Win32, and PowerShell

## Build

```bash
# Install Go 1.22+
# https://go.dev/dl/

cd sidecar

# Build for current platform
make local

# Build for all platforms (from macOS)
make all
```

## Run

```bash
# macOS — install as a launchd agent (starts on login, auto-restarts)
./dist/decisionsai-sidecar \
  --server wss://your-relay-server/ws/sidecar \
  --token YOUR_JWT_TOKEN \
  --user YOUR_APP_USER_ID \
  --install

# Windows — install as a scheduled task (starts on login, auto-restarts)
decisionsai-sidecar.exe ^
  --server wss://your-relay-server/ws/sidecar ^
  --token YOUR_JWT_TOKEN ^
  --user YOUR_APP_USER_ID ^
  --install

# Check status
./dist/decisionsai-sidecar --status

# Uninstall
./dist/decisionsai-sidecar --uninstall

# Run manually (foreground, no install)
./dist/decisionsai-sidecar \
  --server wss://your-relay-server/ws/sidecar \
  --token YOUR_JWT_TOKEN \
  --user YOUR_APP_USER_ID
```

After `--install`:
- macOS: registered as `~/Library/LaunchAgents/net.decisionsai.sidecar.plist` — starts on login, logs to `~/Library/Logs/DecisionsAI/sidecar.log`
- Windows: registered as `DecisionsAI\Sidecar` in Task Scheduler — starts on login

## Getting your token

The relay server issues JWT tokens via `POST /api/telegram/ws-token`. Your DecisionsAI desktop app already does this — check its settings for the sidecar token, or generate one via the API.

## How it works

```
Sidecar (your Mac/PC)              Relay Server
──────────────────────             ─────────────────────────────
Connect to /ws/sidecar ──────────► Register connection
Send sidecar_register  ──────────► Store capabilities

                       ◄────────── tool_call: { id, tool, params }
Execute tool locally
Send tool_result       ──────────► Resolve pending future
                                   Agent loop continues
```

The agent loop in the relay server calls tools, the sidecar executes them, results flow back. The agent sees the result and decides the next action.
