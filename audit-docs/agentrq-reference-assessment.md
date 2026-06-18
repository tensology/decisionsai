# AgentRQ reference assessment

Reference clone:

- `../Reference/agentrq` — [agentrq/agentrq](https://github.com/agentrq/agentrq)

AgentRQ is a standalone human–agent task platform (Go/Fiber backend, Vue frontend). It is **not** a drop-in replacement for Decisions ticket boards or workflows. It is useful as a reference for human-in-the-loop task mechanics, MCP exposure, and how IDE agents pull work and report status.

---

## What AgentRQ is

A workspace holds tasks. Each task has a title, body, assignee (`human` or `agent`), status (`notstarted`, `ongoing`, `blocked`, `completed`, `rejected`, `cron`), and a per-task message thread.

Agents connect through **MCP** (per-workspace HTTP server). Core tools:

- `getNextTask` — agent pulls the next `notstarted` task assigned to it
- `updateTaskStatus` — move task through the lifecycle, including `blocked`
- `reply` / `getTaskMessages` — chat on the task with pagination
- `createTask` — agent can assign work back to the human (optional cron)
- Permission requests — agent asks before a sensitive tool runs; human allow/deny via UI (Slack buttons supported)

**Gateways** push real-time notifications into idle agents:

- Claude Code channel (`claude --dangerously-load-development-channels`)
- `@agentrq/codex-gateway` for Codex app-server
- `@agentrq/acp-gateway` for Gemini CLI and other ACP agents

**Supervisor (CoreMCP)** — OAuth MCP at account scope: list workspaces, `listAllTasks`, global status updates.

---

## What Decisions already has (overlap)

| AgentRQ concept | Decisions today |
|-----------------|-----------------|
| Task queue | Kanban tickets + workflow runs |
| Agent pulls next job | Workflow dispatcher / Step Runner advances steps |
| Human discusses before work | Send ticket to orchestrator (`engage-orchestrator`) |
| Per-task chat | Ticket → chat thread; workflow activity in transcript |
| `blocked` / waiting | `wait_for_continue`, `require_approval`, IDE handoff waiting, steering |
| Cron tasks | Automations scheduler (first-class in 2.8) |
| MCP tool surface | Harness MCP merge into Cursor/Codex; Composio |
| Complexity / routing | Ticket complexity → IDE or CLI backend + model |

Decisions is broader (boards, loops, automations, voice, Telegram, harness). AgentRQ is narrower and deeper on **one loop**: human and agent co-managing a single task list with explicit permission gates.

---

## Where AgentRQ can help workflows (actionable)

### 1. Permission requests (best pattern to borrow)

AgentRQ treats dangerous tool use as a first-class **permission request** on the task thread: pending → allow/deny → agent continues or marks `blocked`.

Decisions workflows already have `require_approval`, `route_approval_pending`, and steering while a step waits on Cursor/Codex, but the active-run UI does not present tool-level allow/deny as clearly as AgentRQ.

**Workflow win:** For high-complexity tickets or steps with `playwright` / `computer_use` / `send_to_project_cli`, surface a compact permission card on the active-run view (tool name, input preview, Allow / Deny) instead of only generic “waiting.”

### 2. Status vocabulary on active runs

AgentRQ statuses are plain: `notstarted` → `ongoing` → `completed` or `blocked`.

**Workflow win:** Map internal run states (`waiting`, `ide_handoff_pending`, `correction_retry`) to a small user-facing set on the active-run screen, with `blocked` meaning “needs you” rather than “engine error.”

### 3. Per-step message thread

Each AgentRQ task keeps its own chat history (`getTaskMessages` with cursor).

**Workflow win:** Attach a readable message list to each workflow step in the active-run view (steering sent, validation result, IDE events) so a loop does not require digging through the main Decisions chat.

### 4. Agent assigns work back to human

`createTask` with `assignee: human` is how the agent queues review, clarification, or follow-up for the operator.

**Workflow win:** After an implementation step passes validation, a loop preset step could create a kanban ticket (or orchestrator nudge) assigned for human QA — same job as Fallow audit gate, but for non-code checkpoints.

### 5. Real-time nudge while IDE is idle

Codex/Claude gateways notify the agent when the human replies or changes task status.

**Workflow win:** When a workflow step is `waiting` on IDE handoff, mirror AgentRQ’s push pattern: Telegram / remote / desktop toast when the human steers or approves, so the orchestrator does not rely on polling alone. Partially exists; AgentRQ is a clean reference for the notification contract.

### 6. Optional external HITL dashboard (integration, not fork)

Teams that want a separate glass UI for stakeholders could add AgentRQ as an **optional** MCP workspace per project: workflow step exports ticket summary → AgentRQ task → human works there → step completes when status is `completed`.

**Cost:** Second system, Google OAuth, sync boundaries. Only worth it for mixed technical/non-technical crews who will not use Decisions boards.

**Not recommended:** Replacing Decisions kanban or automations with AgentRQ workspaces.

---

## Suggested integration tiers

| Tier | Effort | Fit |
|------|--------|-----|
| **A — UX patterns only** | Low | Permission cards, blocked label, per-step thread on active-run. No AgentRQ dependency. |
| **B — Loop preset** | Medium | “Implement → human QA ticket” step using existing kanban + orchestrator (no AgentRQ). |
| **C — MCP bridge step** | High | New workflow action `agentrq_wait` + harness MCP entry; poll/webhook for task completion. |
| **D — Full sidecar product** | Very high | Run AgentRQ beside Decisions. Poor fit unless a customer demands it. |

**Recommendation:** **Tier A now**, **Tier B** aligns with existing loop presets (Fallow audit, orchestrator discuss-first). Treat **Tier C** as optional vendor pack only if there is a concrete user ask.

---

## Relation to What’s to Come

| Roadmap item | AgentRQ reference |
|--------------|-------------------|
| Definitive completion | `updateTaskStatus(completed)` + final `reply` — always names done vs blocked |
| Stall detection | `ongoing` with no agent connection + `blocked` with permission pending |
| Informed retries | Task message thread carries prior failure context into the next agent pull |
| Memory between runs | Workspace mission + task body persist; Decisions should keep ticket + run evidence on the board the same way |

---

## Files worth skimming in the clone

- `README.md` — MCP tools, gateways, Supervisor
- `backend/internal/controller/mcp/server.go` — permission request flow, tool schemas
- `backend/internal/data/model/model.go` — task/status/message model
- `integrations/slack/README.md` — permission allow/deny in Slack (pattern for Telegram)

---

## Bottom line

AgentRQ does not replace Decisions workflows. It is a strong reference for **human-in-the-loop task control**: pull next work, block with reason, chat on the task, and approve tools explicitly. The highest-value borrow for Decisions loops is **permission + blocked + per-step thread on the active-run view**, using existing orchestrator and board primitives rather than shipping a second task product.
