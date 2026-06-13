# Remote App Drift Audit

Last updated: 2026-05-21

Compares **desktop GUI** (`DecisionsAI/distr/gui/web/`) with **remote app** (`www.decisionsai.net/remote-app/`). Transport: WebSocket `api_relay` to desktop HTTP (except file upload/download direct to relay server).

## Terminology

- **Orchestrator** — routes tickets to harnesses (Codex, Cursor, Pi, IDE). User-facing controls live on **Workflows → Runs** (Active Run command center).
- **Orchestrator ledger** — event/history layer under routing (not a separate mobile product).
- **Chat** — agent sessions (`/api/chats`). **Rooms** — IRC shared chat (`/irc/`); intentionally removed from remote nav.

## Feature matrix

| Surface | Desktop reference | Remote reference | Status | Priority |
|---------|-------------------|------------------|--------|----------|
| Workflow queue | `workflows.js` queue panel | `WorkflowQueueTab.jsx` | Partial — button reorder, status pills | P0 |
| Active Run command center | `workflows.js` `renderRunCommandCenter` | `WorkflowRunsTab.jsx` | Partial — approve route, steer, cancel | P0 |
| Run preview before ticket run | `openWorkflowRunPreview` + `cli-context` | `WorkflowsTab.jsx` | Implemented in drift pass | P0 |
| Board intake | Left panel lanes + drag | `WorkflowBoardIntake.jsx` | Partial — local boards, add-all, external browse read-only | P1 |
| Loop steps | Ring/list + editor | `WorkflowLoopTab.jsx` | Read-only display | P2 defer |
| Activity log | Completed runs | `WorkflowActivityTab.jsx` | Read-only list | OK |
| CLI tab | Project terminal WS | `WorkflowCliTab.jsx` | Lite stub (desktop for terminal) | Intentional |
| Orchestrator events timeline | Runs → Events subtab | — | Missing | P2 defer |
| Ticket boards CRUD | `kanban.js` | `KanbanTab.jsx` | Partial | P1 |
| Ticket complexity / report | `kanban_ticket.js` tabs | `KanbanTab.jsx` | Partial → complexity + audit report | P1 |
| Ticket lane move | Drag between lanes | `KanbanTab.jsx` save + `moveTicket` | OK | — |
| Send to workflow | Modal + preview | Kanban + Workflows | OK | — |
| Agent chat | `chat.js` full config | `ChatTab.jsx` | Partial — send, speak, cancel | OK |
| IRC Rooms | `irc.js` | `RelayChatTab.jsx` (removed) | Intentional | — |
| Projects | File upload multipart | `ProjectsTab.jsx` | Upload deferred (relay JSON-only) | P2 |
| Snippets | Hotkey editor | `SnippetsTab.jsx` | `remote_hotkey` editing | P1 |
| API paths | `/api/tickets/*` | `ticketApi.js` | Parity | OK |
| Legacy kanban alias | `server.py` middleware | N/A | Deprecated shim documented | OK |
| Realtime | `WS /api/workflows/ws` | Poll 2.5s in `useWorkflowDetail.js` | Partial | P2 |

## P0 APIs (workflow run controls)

| Action | Method | Path |
|--------|--------|------|
| Active run | GET | `/api/workflows/{id}/active-run` |
| Cancel run | POST | `/api/workflows/{id}/cancel-run/{run_id}` |
| Steer harness | POST | `/api/workflows/{id}/runs/{run_id}/steer` `{ message }` |
| Route approval | POST | `/api/workflows/{id}/runs/{run_id}/route-approval` `{ approved }` |
| Run preview | GET | `/api/tickets/tickets/{id}/cli-context` |
| Start ticket run | POST | `/api/tickets/tickets/{id}/send-to-workflow` |

## Manual test checklist

1. **Boards** → open ticket → Send to workflow.
2. **Workflows** → board intake → Add → queue → **Run** → preview modal → confirm.
3. **Runs tab** shows status, route card, steer, cancel.
4. **Route approval**: when `waiting_kind: route_approval`, Approve override / Use policy route work.
5. **Chat** — create chat, send message, cancel stream while typing.
6. **Rooms** tab absent; no `RelayChatTab` in bundle.
7. `npm run build` in `remote-app` succeeds.

## Out of scope (mobile)

- Full CLI terminal (`WS /api/projects/{id}/terminal/ws`)
- Loop step editor, presets, AI generate-steps
- WhatsApp Messages tab
- Automations, Settings, Docs
- Drag-and-drop queue/board reorder
- Project file upload (until multipart relay proxy exists)
- IRC Rooms tab

## Related docs

- [remote-app/README.md](../../www.decisionsai.net/remote-app/README.md)
- [orchestrator-naming-audit.md](./orchestrator-naming-audit.md)
- [hermes-orchestration-state-2026-05-21.md](./hermes-orchestration-state-2026-05-21.md) (historical; orchestrator rename completed)
