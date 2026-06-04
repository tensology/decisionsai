<!-- decisions-meta: {"project_id":9,"project_name":"DecisionsAI","backend":"cursor_ide","origin":"harness_live_proof","run_id":118,"workflow_id":307,"step_id":1801,"execution_session_id":1,"handoff_event_id":2297,"api_base":"http://127.0.0.1:8765","callback_url":"http://127.0.0.1:8765/api/workflows/307/runs/118/continue","continue_url":"http://127.0.0.1:8765/api/workflows/307/runs/118/continue","bridge_url":"http://127.0.0.1:8765/api/workflows/307/runs/118/codex-events","callback_payload_type":"workflow_continue"} -->
<!-- decisions-ide-meta: {"project_id":9,"project_name":"DecisionsAI","backend":"cursor_ide","origin":"harness_live_proof","run_id":118,"workflow_id":307,"step_id":1801,"execution_session_id":1,"handoff_event_id":2297,"api_base":"http://127.0.0.1:8765","callback_url":"http://127.0.0.1:8765/api/workflows/307/runs/118/continue","continue_url":"http://127.0.0.1:8765/api/workflows/307/runs/118/continue","bridge_url":"http://127.0.0.1:8765/api/workflows/307/runs/118/codex-events","callback_payload_type":"workflow_continue"} -->
---
mode: append
auto_continue_on_pickup: false
callback_payload_type: workflow_continue
---

# DecisionsAI Work Packet

Project: DecisionsAI (9)
Backend: Cursor IDE

## Instruction

HARNESS LIVE PROOF ONLY. Do not edit project files. Open this DecisionsAI work packet in the IDE, verify the callback metadata is present, then report needs_input or completion through the DecisionsAI bridge. This packet exists to prove durable IDE handoff visibility.

## Return Contract

When finished, report back to DecisionsAI with:
- Status: completed | failed | needs_input
- Summary
- Files changed
- Tests run
- Blockers or next step

## Callback

The workflow stays waiting until you report completion.
- VS Code/Cursor command: `DecisionsAI: Report Workflow Complete`
- Resume workflow: POST http://127.0.0.1:8765/api/workflows/307/runs/118/continue
- Bridge events: POST http://127.0.0.1:8765/api/workflows/307/runs/118/codex-events
