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
