# DecisionsAI Extension

A VS Code extension that automatically watches for ticket files in your workspace and submits them to Cursor's chat interface.

## Features

- **Automatic Ticket Processing**: Watches for `.md` and `.txt` files in the `.tickets/` folder
- **Image Support**: Automatically attaches image files (PNG, JPG, GIF, etc.) found in the tickets folder
- **Seamless Integration**: Automatically pastes ticket content into Cursor's chat interface
- **Output Logging**: View all activity in the Output panel (View > Output > DecisionsAI)

## How It Works

1. Place ticket files (`.md` or `.txt`) in a `.tickets/` folder in your workspace root
2. Optionally include image files in the same folder
3. The extension automatically detects and processes these files
4. Ticket content is automatically pasted into Cursor's chat interface
5. Files are deleted after processing to prevent duplicate submissions

## Append To Current Session

If you want a ticket file to continue the currently running ticket/session (instead of starting a fresh task), add one of these fields in YAML frontmatter:

- `mode: append`
- `append_to_current_session: true`
- `continue_current_ticket: true`
- `do_not_start_new_ticket: true`

Example:

```md
---
id: ticket_20260426_105500
mode: append
---

Continue implementing the previous task and only apply this delta:
- update header spacing
- keep existing session context
```

## Workflow Callback Behavior

When a ticket contains a `decisions-meta` or `decisions-ide-meta` HTML comment with callback fields, the extension coordinates with DecisionsAI workflows.

- `callback_url` / `continue_url`: workflow resume endpoint
- `bridge_url`: progress events while the workflow is waiting
- `callback_payload_type: workflow_continue`: completion reports send `{ "input": "..." }`
- `auto_continue_on_pickup: false` (default for DecisionsAI IDE packets): do **not** resume the workflow when the ticket is picked up

Pickup behavior:

1. Extension loads the work packet into Cursor chat
2. Posts `ide_work_started` to `bridge_url`
3. Leaves the workflow waiting

When you finish IDE work, run:

- Command palette → **DecisionsAI: Report Workflow Complete**

Or resume from the DecisionsAI Workflows UI with **Report IDE complete**.

If `callback_url` is missing but workflow metadata exists (`run_id`, `workflow_id`, `api_base`), the extension falls back to:

- `POST /api/workflows/{workflow_id}/runs/{run_id}/continue`

## Requirements

- VS Code 1.105.1 or higher
- Cursor IDE (for chat functionality)
- macOS (uses AppleScript for automation)

## Extension Settings

This extension contributes the following command:

* `decisionsai.activateTicketWatcher`: Manually activate the ticket watcher

## Known Issues

- Currently optimized for macOS. Windows/Linux support may require adjustments to the automation scripts.

## Release Notes

### 0.0.1

Initial release of DecisionsAI extension with automatic ticket file watching and Cursor chat integration.
