# Legal, Privacy, And Terms Review

Date checked: 2026-05-22

This is an implementation audit note, not legal advice.

## Public Pages Checked

| Page | URL | Result |
|---|---|---|
| Privacy Policy | https://www.decisionsai.net/privacy | HTTP 200, but fetched content appeared to be the public app/home shell rather than a distinct privacy document. No visible last-updated date was found. |
| Terms and Conditions | https://www.decisionsai.net/terms | HTTP 200, but fetched content appeared to be the public app/home shell rather than a distinct terms document. No visible last-updated date was found. |

## Gap

The public website needs visible, route-specific legal documents for Privacy
Policy and Terms and Conditions. The README and Codex plugin now reference those
URLs, but the website itself should expose the actual policy text and a visible
last-updated date for each document.

## Coverage To Add Or Confirm

The Privacy Policy and Terms should cover:

| Area | Why It Matters |
|---|---|
| Connected accounts | Google, Gmail, Telegram, WhatsApp, Jira, Trello, and similar integrations can expose third-party data. |
| IRC and shared chat rooms | Shared rooms can contain messages from people who are not the account owner. The policy should explain capture, retention, and responsibility. |
| WhatsApp and Telegram media | Tickets can include messages, images, PDFs, documents, audio, video, and voice notes. |
| Voice-note transcription | Audio may be transcribed locally or through a configured provider; this should be disclosed. |
| OCR and image handling | Images may be attached, previewed, or inspected by an agent/model during ticket composition and validation. |
| Project folders and indexing | Local project files, uploaded files, and indexed folders can be read by the agent when configured. |
| CLI/IDE execution | Ticket execution can run commands, create branches, modify files, capture stdout/stderr, and store completion evidence. |
| Model providers | Requests may be sent to configured local or cloud model providers depending on user setup and ticket complexity. |
| Workflow audit trails | Runs, events, validations, corrections, approvals, failures, and elapsed times are retained for traceability. |
| Internal orchestration memory | Hermes records run events, validation records, correction attempts, runtime sessions, and optional portable memory exports. |
| Human approval and liability | The product should clarify when the user must review outbound communication, file changes, external messages, or autonomous actions. |
| Data deletion and retention | Users need to know how to clear ticket files, message bindings, workflow runs, activity logs, and orchestration memory. |

## README Updates Made

The root README and Codex plugin README now reference:

- Privacy Policy: https://www.decisionsai.net/privacy
- Terms and Conditions: https://www.decisionsai.net/terms
- README check date: 2026-05-22

Hermes is also listed as technical accreditation in the developer-facing docs,
while the web UI should continue to use user-facing language such as workflow
orchestration, validation, run history, and correction memory.
