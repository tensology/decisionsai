"""
API Documentation routes
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


def create_routes(base_path: str = "") -> APIRouter:
    router = APIRouter()

    @router.get("/endpoints")
    async def get_api_endpoints():
        """Return the full API endpoint catalogue for the docs UI."""
        base = "http://127.0.0.1:8765"
        endpoints = [
            # ── Chats ──────────────────────────────────────────────
            {
                "section": "Chats",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/api/chats",
                        "summary": "List all chats",
                        "description": "Returns all root-level chat conversations, ordered by most recently modified. Also returns last_chat_id and agent_current_chat_id from settings.",
                        "curl": f'curl -s {base}/api/chats',
                        "body": None,
                        "response_example": '{"chats": [{"id": 1, "title": "New Chat", "provider": "Ollama", "model_name": "qwen3:8b"}], "last_chat_id": 1, "agent_current_chat_id": 1}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/chats/{chat_id}",
                        "summary": "Get chat with messages",
                        "description": "Returns a specific chat including its full message history (recursive thread), provider, model, and voice settings.",
                        "curl": f'curl -s {base}/api/chats/1',
                        "body": None,
                        "params": [{"name": "chat_id", "type": "int", "required": True, "description": "Chat ID"}],
                        "response_example": '{"id": 1, "title": "New Chat", "messages": [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi!"}], "provider": "Ollama", "model_name": "qwen3:8b"}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/chats",
                        "summary": "Create a new chat",
                        "description": "Creates a new chat conversation. Optionally specify provider, model, voice, title, and a starting question. If starting_question is provided, it will be sent to the agent immediately.",
                        "curl": f"""curl -s -X POST {base}/api/chats \\
  -H 'Content-Type: application/json' \\
  -d '{{"provider": "ollama", "model_name": "qwen3:8b", "title": "My Chat", "starting_question": "Hello!"}}'""",
                        "body": {
                            "title": {"type": "string", "required": False, "description": "Chat title (auto-generated from question if omitted)"},
                            "provider": {"type": "string", "required": False, "description": "LLM provider: ollama, openai, anthropic, groq, openrouter, kilocode, gemini"},
                            "model_name": {"type": "string", "required": False, "description": "Model name (e.g. qwen3:8b, gpt-4o)"},
                            "voice_provider": {"type": "string", "required": False, "description": "TTS provider: kokoro, openai, elevenlabs, f5tts"},
                            "voice_model": {"type": "string", "required": False, "description": "Voice model (e.g. af_heart, alloy)"},
                            "starting_question": {"type": "string", "required": False, "description": "First message to send to the agent"},
                            "speak": {"type": "boolean", "required": False, "description": "Whether agent should speak the reply (default: true)"},
                        },
                        "response_example": '{"id": 5, "title": "My Chat", "provider": "Ollama", "model_name": "qwen3:8b", "message": "Chat created successfully"}',
                    },
                    {
                        "method": "PATCH",
                        "path": "/api/chats/{chat_id}",
                        "summary": "Update a chat",
                        "description": "Update chat title, provider, model, or voice settings. Only provided fields are updated.",
                        "curl": f"""curl -s -X PATCH {base}/api/chats/1 \\
  -H 'Content-Type: application/json' \\
  -d '{{"title": "Renamed Chat"}}'""",
                        "body": {
                            "title": {"type": "string", "required": False, "description": "New title"},
                            "provider": {"type": "string", "required": False, "description": "New LLM provider"},
                            "model_name": {"type": "string", "required": False, "description": "New model name"},
                            "voice_provider": {"type": "string", "required": False, "description": "New voice provider"},
                            "voice_model": {"type": "string", "required": False, "description": "New voice model"},
                        },
                        "params": [{"name": "chat_id", "type": "int", "required": True, "description": "Chat ID"}],
                        "response_example": '{"id": 1, "title": "Renamed Chat", "message": "Chat updated"}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/chats/{chat_id}",
                        "summary": "Delete a chat",
                        "description": "Permanently deletes a chat and all its child messages.",
                        "curl": f'curl -s -X DELETE {base}/api/chats/1',
                        "params": [{"name": "chat_id", "type": "int", "required": True, "description": "Chat ID"}],
                        "body": None,
                        "response_example": '{"message": "Chat deleted successfully"}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/chats/{chat_id}/load-in-agent",
                        "summary": "Load chat into agent",
                        "description": "Sets this chat as the active chat in the agent. The agent will hot-swap to this chat's provider/model/voice. You MUST load a chat before sending messages to it.",
                        "curl": f'curl -s -X POST {base}/api/chats/1/load-in-agent',
                        "params": [{"name": "chat_id", "type": "int", "required": True, "description": "Chat ID"}],
                        "body": None,
                        "response_example": '{"loaded": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/chats/{chat_id}/send-to-agent",
                        "summary": "Send message to agent",
                        "description": "Sends a message to the agent for LLM processing. The chat MUST be loaded first (via load-in-agent). The agent will generate a response, optionally execute tools, and stream the result back.",
                        "curl": f"""curl -s -X POST {base}/api/chats/1/send-to-agent \\
  -H 'Content-Type: application/json' \\
  -d '{{"message": "What time is it?", "speak": false}}'""",
                        "body": {
                            "message": {"type": "string", "required": True, "description": "The message to send"},
                            "speak": {"type": "boolean", "required": False, "description": "Whether agent should speak the reply via TTS (default: true)"},
                            "provider": {"type": "string", "required": False, "description": "Override LLM provider for this message"},
                            "model_name": {"type": "string", "required": False, "description": "Override model for this message"},
                        },
                        "params": [{"name": "chat_id", "type": "int", "required": True, "description": "Chat ID (must be loaded first)"}],
                        "response_example": '{"sent": true}',
                    },
                ],
            },
            # ── Actions ────────────────────────────────────────────
            {
                "section": "Actions",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/api/actions",
                        "summary": "List all actions",
                        "description": "Returns all recorded actions (macros). Each action has an ID, title, and list of steps.",
                        "curl": f'curl -s {base}/api/actions',
                        "body": None,
                        "response_example": '{"actions": [{"id": 1, "title": "Open Browser", "steps": [...]}]}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/actions/{action_id}",
                        "summary": "Get action details",
                        "description": "Returns a specific action with all its steps.",
                        "curl": f'curl -s {base}/api/actions/1',
                        "params": [{"name": "action_id", "type": "int", "required": True, "description": "Action ID"}],
                        "body": None,
                        "response_example": '{"id": 1, "title": "Open Browser", "steps": [{"type": "click", "x": 100, "y": 200}]}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/actions",
                        "summary": "Create an action",
                        "description": "Creates a new action with a title and optional steps.",
                        "curl": f"""curl -s -X POST {base}/api/actions \\
  -H 'Content-Type: application/json' \\
  -d '{{"title": "My Action", "steps": []}}'""",
                        "body": {
                            "title": {"type": "string", "required": True, "description": "Action title"},
                            "steps": {"type": "array", "required": False, "description": "List of action steps"},
                        },
                        "response_example": '{"id": 3, "title": "My Action", "message": "Action created"}',
                    },
                    {
                        "method": "PUT",
                        "path": "/api/actions/{action_id}",
                        "summary": "Update an action",
                        "description": "Update an action's title or steps.",
                        "curl": f"""curl -s -X PUT {base}/api/actions/1 \\
  -H 'Content-Type: application/json' \\
  -d '{{"title": "Renamed Action"}}'""",
                        "params": [{"name": "action_id", "type": "int", "required": True, "description": "Action ID"}],
                        "body": {
                            "title": {"type": "string", "required": False, "description": "New title"},
                            "steps": {"type": "array", "required": False, "description": "New steps"},
                        },
                        "response_example": '{"id": 1, "title": "Renamed Action", "message": "Action updated"}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/actions/{action_id}",
                        "summary": "Delete an action",
                        "description": "Permanently deletes an action.",
                        "curl": f'curl -s -X DELETE {base}/api/actions/1',
                        "params": [{"name": "action_id", "type": "int", "required": True, "description": "Action ID"}],
                        "body": None,
                        "response_example": '{"message": "Action deleted"}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/actions/{action_id}/play",
                        "summary": "Play (run) an action",
                        "description": "Executes a recorded action. The action will replay its steps (clicks, keystrokes, etc.).",
                        "curl": f'curl -s -X POST {base}/api/actions/1/play',
                        "params": [{"name": "action_id", "type": "int", "required": True, "description": "Action ID"}],
                        "body": None,
                        "response_example": '{"message": "Action started"}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/actions/start-recording",
                        "summary": "Start recording an action",
                        "description": "Begins recording user input (mouse clicks, keystrokes) as a new action. Call stop-recording to finish.",
                        "curl": f'curl -s -X POST {base}/api/actions/start-recording',
                        "body": None,
                        "response_example": '{"message": "Recording started", "action_id": 4}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/actions/stop-recording",
                        "summary": "Stop recording an action",
                        "description": "Stops the current recording session. You must provide a title for the recorded action. Returns an error if no recording is in progress.",
                        "curl": f"""curl -s -X POST {base}/api/actions/stop-recording \\
  -H 'Content-Type: application/json' \\
  -d '{{"title": "My Recorded Action"}}'""",
                        "body": {
                            "title": {"type": "string", "required": True, "description": "Name for the recorded action"},
                        },
                        "response_example": '{"message": "Recording stopped", "action_id": 4}',
                    },
                ],
            },
            # ── Projects ───────────────────────────────────────────
            {
                "section": "Projects",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/api/projects",
                        "summary": "List all projects",
                        "description": "Returns all projects with their metadata.",
                        "curl": f'curl -s {base}/api/projects',
                        "body": None,
                        "response_example": '{"projects": [{"id": 1, "name": "My App", "path": "/Users/me/projects/myapp"}]}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/projects/{project_id}",
                        "summary": "Get project details",
                        "description": "Returns a specific project with all its context items and files.",
                        "curl": f'curl -s {base}/api/projects/1',
                        "params": [{"name": "project_id", "type": "int", "required": True, "description": "Project ID"}],
                        "body": None,
                        "response_example": '{"id": 1, "name": "My App", "files": [...], "context_items": [...]}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/projects",
                        "summary": "Create a project",
                        "description": "Creates a new project.",
                        "curl": f"""curl -s -X POST {base}/api/projects \\
  -H 'Content-Type: application/json' \\
  -d '{{"name": "New Project", "path": "/path/to/project"}}'""",
                        "body": {
                            "name": {"type": "string", "required": True, "description": "Project name"},
                            "path": {"type": "string", "required": False, "description": "Project folder path"},
                        },
                        "response_example": '{"id": 2, "name": "New Project", "message": "Project created"}',
                    },
                    {
                        "method": "PUT",
                        "path": "/api/projects/{project_id}",
                        "summary": "Update a project",
                        "description": "Update a project's name or settings.",
                        "curl": f"""curl -s -X PUT {base}/api/projects/1 \\
  -H 'Content-Type: application/json' \\
  -d '{{"name": "Renamed Project"}}'""",
                        "params": [{"name": "project_id", "type": "int", "required": True, "description": "Project ID"}],
                        "body": {
                            "name": {"type": "string", "required": False, "description": "New name"},
                        },
                        "response_example": '{"id": 1, "name": "Renamed Project", "message": "Project updated"}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/projects/{project_id}",
                        "summary": "Delete a project",
                        "description": "Permanently deletes a project and its associated data.",
                        "curl": f'curl -s -X DELETE {base}/api/projects/1',
                        "params": [{"name": "project_id", "type": "int", "required": True, "description": "Project ID"}],
                        "body": None,
                        "response_example": '{"message": "Project deleted"}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/projects/{project_id}/use",
                        "summary": "Activate a project",
                        "description": "Sets this project as the active project. The agent will use its context for RAG queries.",
                        "curl": f'curl -s -X POST {base}/api/projects/1/use',
                        "params": [{"name": "project_id", "type": "int", "required": True, "description": "Project ID"}],
                        "body": None,
                        "response_example": '{"message": "Project activated"}',
                    },
                ],
            },
            # ── Workflows (unified automation engine) ─────────────
            {
                "section": "Workflows",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/api/workflows",
                        "summary": "List all workflows",
                        "description": "Returns all workflows. Excludes audit type by default. Filter by type or search text.",
                        "curl": f'curl -s {base}/api/workflows',
                        "params": [
                            {"name": "limit", "type": "int", "required": False, "description": "Max results (default 50)"},
                            {"name": "type", "type": "string", "required": False, "description": "Filter: manual, instruction, scheduled, or audit"},
                            {"name": "search", "type": "string", "required": False, "description": "Search text"},
                        ],
                        "body": None,
                        "response_example": '[{"id": 1, "name": "Deploy pipeline", "workflow_type": "instruction", "step_count": 4, "schedule_enabled": false}]',
                    },
                    {
                        "method": "GET",
                        "path": "/api/workflows/{workflow_id}",
                        "summary": "Get workflow details",
                        "description": "Returns a specific workflow with all its steps.",
                        "curl": f'curl -s {base}/api/workflows/1',
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": None,
                        "response_example": '{"id": 1, "name": "Deploy pipeline", "context_rules": "Use production-safe commands only.", "steps": [{"id": 1, "name": "Build", "instruction": "Run npm build", "status": "pending"}]}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/workflows/plan",
                        "summary": "Plan a workflow from instruction",
                        "description": "Break down an instruction into steps using the LLM and create a workflow.",
                        "curl": f"""curl -s -X POST {base}/api/workflows/plan \\
  -H 'Content-Type: application/json' \\
  -d '{{"instruction": "Deploy the app to production", "chat_id": null}}'""",
                        "body": {
                            "instruction": {"type": "string", "required": True, "description": "Instruction to break into steps"},
                            "chat_id": {"type": "int", "required": False, "description": "Optional chat ID to link"},
                        },
                        "response_example": '{"id": 3, "name": "Deploy the app to production", "context_rules": "", "steps": [...]}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/workflows/{workflow_id}",
                        "summary": "Delete a workflow",
                        "description": "Permanently deletes a workflow and its steps.",
                        "curl": f'curl -s -X DELETE {base}/api/workflows/1',
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": None,
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/workflows/{workflow_id}/run",
                        "summary": "Run all steps in a workflow",
                        "description": "Starts executing all steps sequentially. The agent processes each step and advances automatically. Resets all steps to pending first.",
                        "curl": f'curl -s -X POST {base}/api/workflows/1/run',
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": {
                            "start_step_id": {"type": "int", "required": False, "description": "Optional step ID to start from"},
                        },
                        "response_example": '{"success": true, "message": "Running all steps in sequence"}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/workflows/{workflow_id}/runs/{run_id}/continue",
                        "summary": "Continue a waiting step",
                        "description": "Resumes a step that is in 'waiting' status. Optionally pass 'input' to provide additional context.",
                        "curl": f"""curl -s -X POST {base}/api/workflows/1/runs/1/continue \\
  -H 'Content-Type: application/json' \\
  -d '{{"input": "The deployment finished successfully"}}'""",
                        "params": [
                            {"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"},
                            {"name": "run_id", "type": "int", "required": True, "description": "Run ID"},
                        ],
                        "body": {
                            "input": {"type": "string", "required": False, "description": "Optional context/data to resume with"},
                        },
                        "response_example": '{"success": true, "message": "Continue signal sent"}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/workflows/{workflow_id}/active-run",
                        "summary": "Get active run for a workflow",
                        "description": "Returns the active run for a workflow if one is running or waiting.",
                        "curl": f'curl -s {base}/api/workflows/1/active-run',
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": None,
                        "response_example": '{"id": 17, "current_step_id": 4, "started_at": "2026-04-20T10:00:00"}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/workflows/active-runs",
                        "summary": "List active runs across workflows",
                        "description": "Returns active workflow runs enriched with board, ticket, phase, step, and elapsed metadata.",
                        "curl": f'curl -s "{base}/api/workflows/active-runs?limit=50"',
                        "params": [
                            {"name": "limit", "type": "int", "required": False, "description": "Max results (default 50)"},
                            {"name": "workflow_id", "type": "int", "required": False, "description": "Filter to a single workflow"},
                        ],
                        "body": None,
                        "response_example": '[{"id": 99, "workflow_id": 1, "workflow_name": "Development", "status": "running", "phase": "execution"}]',
                    },
                    {
                        "method": "POST",
                        "path": "/api/workflows/{workflow_id}/duplicate",
                        "summary": "Duplicate a workflow",
                        "description": "Creates a copy of the workflow and its steps.",
                        "curl": f'curl -s -X POST {base}/api/workflows/1/duplicate',
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": None,
                        "response_example": '{"id": 5, "name": "...", "context_rules": "", "steps": [...]}',
                    },
                    {
                        "method": "PATCH",
                        "path": "/api/workflows/{workflow_id}/steps/reorder",
                        "summary": "Reorder steps",
                        "description": "Reorder steps within a workflow by providing the new order of step IDs.",
                        "curl": f"""curl -s -X PATCH {base}/api/workflows/1/steps/reorder \\
  -H 'Content-Type: application/json' \\
  -d '{{"step_ids": [3, 1, 2]}}'""",
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": {
                            "step_ids": {"type": "array", "required": True, "description": "Ordered list of step IDs"},
                        },
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "PATCH",
                        "path": "/api/workflows/{workflow_id}/schedule",
                        "summary": "Update schedule",
                        "description": "Update a workflow's schedule configuration.",
                        "curl": f"""curl -s -X PATCH {base}/api/workflows/1/schedule \\
  -H 'Content-Type: application/json' \\
  -d '{{"enabled": false}}'""",
                        "params": [{"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"}],
                        "body": {
                            "enabled": {"type": "bool", "required": False, "description": "Enable/disable"},
                            "schedule": {"type": "string", "required": False, "description": "New schedule"},
                            "schedule_time": {"type": "string", "required": False, "description": "New time"},
                            "schedule_days": {"type": "string", "required": False, "description": "New days"},
                            "timezone": {"type": "string", "required": False, "description": "New timezone"},
                        },
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/workflows/{workflow_id}/runs",
                        "summary": "Get run history",
                        "description": "Returns run history for a workflow.",
                        "curl": f'curl -s {base}/api/workflows/1/runs',
                        "params": [
                            {"name": "workflow_id", "type": "int", "required": True, "description": "Workflow ID"},
                            {"name": "limit", "type": "int", "required": False, "description": "Max results (default 10)"},
                        ],
                        "body": None,
                        "response_example": '[{"id": 1, "workflow_id": 1, "status": "completed", "started_at": "...", "completed_at": "..."}]',
                    },
                    {
                        "method": "GET",
                        "path": "/api/workflows/version",
                        "summary": "Get version counter",
                        "description": "Returns a version counter that increments when workflow data changes. UI polls this to know when to refresh.",
                        "curl": f'curl -s {base}/api/workflows/version',
                        "body": None,
                        "response_example": '{"version": 42}',
                    },
                ],
            },
            # ── Ticket Boards ──────────────────────────────────────
            {
                "section": "Ticket Boards",
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/api/kanban/boards",
                        "summary": "List all boards",
                        "description": "Returns all Ticket boards.",
                        "curl": f'curl -s {base}/api/kanban/boards',
                        "body": None,
                        "response_example": '[{"id": 1, "name": "My Board", "description": "..."}]',
                    },
                    {
                        "method": "POST",
                        "path": "/api/kanban/boards",
                        "summary": "Create a board",
                        "description": "Creates a new Ticket board with default lanes (To Do, In Progress, Done).",
                        "curl": f"""curl -s -X POST {base}/api/kanban/boards \\
  -H 'Content-Type: application/json' \\
  -d '{{"name": "Sprint Board", "description": "Current sprint"}}'""",
                        "body": {
                            "name": {"type": "string", "required": True, "description": "Board name"},
                            "description": {"type": "string", "required": False, "description": "Board description"},
                        },
                        "response_example": '{"id": 2, "name": "Sprint Board"}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/kanban/boards/{board_id}",
                        "summary": "Get board with lanes and tickets",
                        "description": "Returns a board with all its lanes and tickets.",
                        "curl": f'curl -s {base}/api/kanban/boards/1',
                        "params": [{"name": "board_id", "type": "int", "required": True, "description": "Board ID"}],
                        "body": None,
                        "response_example": '{"id": 1, "name": "My Board", "lanes": [{"id": 1, "name": "To Do", "tickets": [...]}]}',
                    },
                    {
                        "method": "PUT",
                        "path": "/api/kanban/boards/{board_id}",
                        "summary": "Update a board",
                        "description": "Update board name, description, default project, or agent settings.",
                        "curl": f"""curl -s -X PUT {base}/api/kanban/boards/1 \\
  -H 'Content-Type: application/json' \\
  -d '{{"name": "Updated Board"}}'""",
                        "params": [{"name": "board_id", "type": "int", "required": True, "description": "Board ID"}],
                        "body": {
                            "name": {"type": "string", "required": False, "description": "Board name"},
                            "description": {"type": "string", "required": False, "description": "Description"},
                            "default_project_id": {"type": "int", "required": False, "description": "Default project ID"},
                        },
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/kanban/boards/{board_id}",
                        "summary": "Delete a board",
                        "description": "Permanently deletes a board and all its tickets.",
                        "curl": f'curl -s -X DELETE {base}/api/kanban/boards/1',
                        "params": [{"name": "board_id", "type": "int", "required": True, "description": "Board ID"}],
                        "body": None,
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/kanban/tickets",
                        "summary": "Create a ticket",
                        "description": "Creates a new ticket in a lane.",
                        "curl": f"""curl -s -X POST {base}/api/kanban/tickets \\
  -H 'Content-Type: application/json' \\
  -d '{{"lane_id": 1, "title": "Fix login bug", "description": "Users cannot log in", "priority": "high"}}'""",
                        "body": {
                            "lane_id": {"type": "int", "required": True, "description": "Lane ID"},
                            "title": {"type": "string", "required": True, "description": "Ticket title"},
                            "description": {"type": "string", "required": False, "description": "Ticket description"},
                            "priority": {"type": "string", "required": False, "description": "low, medium, high, critical"},
                        },
                        "response_example": '{"id": 5, "title": "Fix login bug", "lane_id": 1}',
                    },
                    {
                        "method": "GET",
                        "path": "/api/kanban/tickets/{ticket_id}",
                        "summary": "Get ticket details",
                        "description": "Returns ticket with files, links, and todos.",
                        "curl": f'curl -s {base}/api/kanban/tickets/1',
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": None,
                        "response_example": '{"id": 1, "title": "Fix login bug", "files": [], "links": [], "todos": []}',
                    },
                    {
                        "method": "PUT",
                        "path": "/api/kanban/tickets/{ticket_id}",
                        "summary": "Update a ticket",
                        "description": "Update ticket title, description, priority, lane, or linked project.",
                        "curl": f"""curl -s -X PUT {base}/api/kanban/tickets/1 \\
  -H 'Content-Type: application/json' \\
  -d '{{"title": "Updated title", "priority": "critical"}}'""",
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": {
                            "title": {"type": "string", "required": False, "description": "Title"},
                            "description": {"type": "string", "required": False, "description": "Description"},
                            "priority": {"type": "string", "required": False, "description": "Priority"},
                            "lane_id": {"type": "int", "required": False, "description": "Move to lane"},
                            "linked_project_id": {"type": "int", "required": False, "description": "Link to project"},
                        },
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "PUT",
                        "path": "/api/kanban/tickets/{ticket_id}/move",
                        "summary": "Move ticket to lane",
                        "description": "Move a ticket to a different lane and optionally set position.",
                        "curl": f"""curl -s -X PUT {base}/api/kanban/tickets/1/move \\
  -H 'Content-Type: application/json' \\
  -d '{{"lane_id": 2, "position": 0}}'""",
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": {
                            "lane_id": {"type": "int", "required": True, "description": "Target lane ID"},
                            "position": {"type": "int", "required": False, "description": "Position in lane"},
                        },
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/kanban/tickets/{ticket_id}",
                        "summary": "Delete a ticket",
                        "description": "Permanently deletes a ticket.",
                        "curl": f'curl -s -X DELETE {base}/api/kanban/tickets/1',
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": None,
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/kanban/tickets/{ticket_id}/files",
                        "summary": "Upload file to ticket",
                        "description": "Attach a file to a ticket (multipart form upload).",
                        "curl": f'curl -s -X POST {base}/api/kanban/tickets/1/files -F "file=@screenshot.png"',
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": None,
                        "response_example": '{"id": 1, "filename": "screenshot.png"}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/kanban/tickets/{ticket_id}/files/{file_id}",
                        "summary": "Delete ticket file",
                        "description": "Remove an attached file from a ticket.",
                        "curl": f'curl -s -X DELETE {base}/api/kanban/tickets/1/files/1',
                        "params": [
                            {"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"},
                            {"name": "file_id", "type": "int", "required": True, "description": "File ID"},
                        ],
                        "body": None,
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/kanban/tickets/{ticket_id}/links",
                        "summary": "Add link to ticket",
                        "description": "Add a URL link to a ticket.",
                        "curl": f"""curl -s -X POST {base}/api/kanban/tickets/1/links \\
  -H 'Content-Type: application/json' \\
  -d '{{"title": "GitHub Issue", "url": "https://github.com/org/repo/issues/42"}}'""",
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": {
                            "title": {"type": "string", "required": True, "description": "Link title"},
                            "url": {"type": "string", "required": True, "description": "URL"},
                        },
                        "response_example": '{"id": 1, "title": "GitHub Issue", "url": "..."}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/kanban/tickets/{ticket_id}/links/{link_id}",
                        "summary": "Delete ticket link",
                        "description": "Remove a link from a ticket.",
                        "curl": f'curl -s -X DELETE {base}/api/kanban/tickets/1/links/1',
                        "params": [
                            {"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"},
                            {"name": "link_id", "type": "int", "required": True, "description": "Link ID"},
                        ],
                        "body": None,
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/kanban/tickets/{ticket_id}/todos",
                        "summary": "Add todo to ticket",
                        "description": "Add a checklist item to a ticket.",
                        "curl": f"""curl -s -X POST {base}/api/kanban/tickets/1/todos \\
  -H 'Content-Type: application/json' \\
  -d '{{"text": "Write unit tests"}}'""",
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": {
                            "text": {"type": "string", "required": True, "description": "Todo text"},
                        },
                        "response_example": '{"id": 1, "text": "Write unit tests", "done": false}',
                    },
                    {
                        "method": "PUT",
                        "path": "/api/kanban/tickets/{ticket_id}/todos/{todo_id}",
                        "summary": "Update todo",
                        "description": "Update a todo's text or toggle its done state.",
                        "curl": f"""curl -s -X PUT {base}/api/kanban/tickets/1/todos/1 \\
  -H 'Content-Type: application/json' \\
  -d '{{"done": true}}'""",
                        "params": [
                            {"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"},
                            {"name": "todo_id", "type": "int", "required": True, "description": "Todo ID"},
                        ],
                        "body": {
                            "text": {"type": "string", "required": False, "description": "New text"},
                            "done": {"type": "bool", "required": False, "description": "Toggle done state"},
                        },
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "DELETE",
                        "path": "/api/kanban/tickets/{ticket_id}/todos/{todo_id}",
                        "summary": "Delete todo",
                        "description": "Remove a checklist item from a ticket.",
                        "curl": f'curl -s -X DELETE {base}/api/kanban/tickets/1/todos/1',
                        "params": [
                            {"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"},
                            {"name": "todo_id", "type": "int", "required": True, "description": "Todo ID"},
                        ],
                        "body": None,
                        "response_example": '{"success": true}',
                    },
                    {
                        "method": "POST",
                        "path": "/api/kanban/tickets/{ticket_id}/send-to-project",
                        "summary": "Send ticket to project",
                        "description": "Export ticket as a markdown file to the linked project's .tickets folder.",
                        "curl": f'curl -s -X POST {base}/api/kanban/tickets/1/send-to-project',
                        "params": [{"name": "ticket_id", "type": "int", "required": True, "description": "Ticket ID"}],
                        "body": None,
                        "response_example": '{"success": true, "path": "/path/to/.tickets/fix-login-bug.md"}',
                    },
                ],
            },
            # ── Models & LLMs ──────────────────────────────────
            {
                "section": "Models & LLMs",
                "endpoints": [
                    {"method": "GET", "path": "/api/models", "summary": "Get default LLM model", "description": "Returns the default conversational LLM provider and model from settings.", "curl": f'curl -s {base}/api/models', "body": None, "response_example": '{"provider": "Ollama", "model": "qwen3:8b"}'},
                    {"method": "GET", "path": "/api/chats/agent-setup", "summary": "Get current agent setup", "description": "Returns the current agent configuration (model + voice) from settings.", "curl": f'curl -s {base}/api/chats/agent-setup', "body": None, "response_example": '{"provider": "Ollama", "model_name": "qwen3:8b", "voice_provider": "Kokoro", "voice_model": "Heart"}'},
                    {"method": "GET", "path": "/api/llms", "summary": "Get LLM settings", "description": "Returns the current LLM provider, model, and related configuration.", "curl": f'curl -s {base}/api/llms', "body": None, "response_example": '{"provider": "Ollama", "model_name": "qwen3:8b", "ollama_url": "http://localhost:11434/"}'},
                    {"method": "POST", "path": "/api/llms", "summary": "Save LLM settings", "description": "Update the default LLM provider and model. Triggers agent reload.", "curl": f"curl -s -X POST {base}/api/llms -H 'Content-Type: application/json' -d '{{\"provider\": \"Ollama\", \"model_name\": \"qwen3:8b\"}}'", "body": {"provider": {"type": "string", "required": True, "description": "LLM provider: Ollama, OpenAI, Anthropic, Groq, OpenRouter, KiloCode, Google Gemini"}, "model_name": {"type": "string", "required": True, "description": "Model name (e.g. qwen3:8b, gpt-4o)"}}, "response_example": '{"success": true, "message": "LLM settings saved"}'},
                    {"method": "GET", "path": "/api/llms/models", "summary": "List available models for a provider", "description": "Returns models available for the given provider. For Ollama, queries the local server.", "curl": f'curl -s "{base}/api/llms/models?provider=Ollama"', "params": [{"name": "provider", "type": "string", "required": True, "description": "Provider name"}], "body": None, "response_example": '{"models": [{"name": "qwen3:8b", "size": "4.9 GB"}]}'},
                    {"method": "GET", "path": "/api/llms/available-providers", "summary": "List enabled LLM providers", "description": "Returns providers that have valid API keys configured or are locally available (Ollama).", "curl": f'curl -s {base}/api/llms/available-providers', "body": None, "response_example": '{"providers": ["Ollama", "OpenAI", "Groq"]}'},
                    {"method": "GET", "path": "/api/llms/recommendations", "summary": "Get model recommendations", "description": "Returns AI-curated model recommendations based on system RAM and available providers.", "curl": f'curl -s {base}/api/llms/recommendations', "body": None, "response_example": '{"recommendations": [{"provider": "Ollama", "model": "qwen3:8b", "tier": "free"}]}'},
                    {"method": "POST", "path": "/api/ollama/pull", "summary": "Pull an Ollama model", "description": "Downloads a model from the Ollama library. Returns immediately; poll Ollama API for progress.", "curl": f"curl -s -X POST {base}/api/ollama/pull -H 'Content-Type: application/json' -d '{{\"model\": \"qwen3:8b\"}}'", "body": {"model": {"type": "string", "required": True, "description": "Model name to pull"}}, "response_example": '{"success": true, "message": "Pull started for qwen3:8b"}'},
                    {"method": "GET", "path": "/api/ollama/library", "summary": "Browse Ollama model library", "description": "Returns the cached Ollama model library with sizes and descriptions.", "curl": f'curl -s {base}/api/ollama/library', "body": None, "response_example": '{"models": [{"name": "qwen3", "tags": ["8b", "1.7b"]}]}'},
                ],
            },
            # ── Voices & TTS ───────────────────────────────────
            {
                "section": "Voices & TTS",
                "endpoints": [
                    {"method": "GET", "path": "/api/tts/providers", "summary": "List all TTS providers with voices", "description": "Returns all enabled TTS providers with their full voice lists. Single source of truth for voice dropdowns.", "curl": f'curl -s {base}/api/tts/providers', "body": None, "response_example": '[{"id": "kokoro", "name": "Kokoro (Offline)", "voices": [{"id": "af_heart", "name": "Heart"}], "supports_custom_voices": true}]'},
                    {"method": "GET", "path": "/api/voices/kokoro", "summary": "List Kokoro voices", "description": "Returns all Kokoro voices including custom cloned voices.", "curl": f'curl -s {base}/api/voices/kokoro', "body": None, "response_example": '[{"id": "af_heart", "name": "Heart"}]'},
                    {"method": "GET", "path": "/api/voices/elevenlabs", "summary": "List ElevenLabs voices", "description": "Returns ElevenLabs voices from their API. Requires a valid ElevenLabs API key.", "curl": f'curl -s {base}/api/voices/elevenlabs', "body": None, "response_example": '[{"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"}]'},
                    {"method": "GET", "path": "/api/voices/openai", "summary": "List OpenAI TTS voices", "description": "Returns the fixed set of OpenAI TTS voices.", "curl": f'curl -s {base}/api/voices/openai', "body": None, "response_example": '[{"id": "alloy", "name": "Alloy"}, {"id": "nova", "name": "Nova"}]'},
                    {"method": "POST", "path": "/api/play-voice", "summary": "Preview a voice", "description": "Generates a short TTS sample and returns it as an MP3 file.", "curl": f"curl -s -X POST {base}/api/play-voice -H 'Content-Type: application/json' -d '{{\"provider\": \"kokoro\", \"voice\": \"af_heart\", \"speed\": 1.0}}' --output sample.mp3", "body": {"provider": {"type": "string", "required": True, "description": "TTS provider: kokoro, elevenlabs, openai"}, "voice": {"type": "string", "required": True, "description": "Voice ID"}, "speed": {"type": "float", "required": False, "description": "Playback speed 0.5-2.0 (default 1.0)"}, "voice_name": {"type": "string", "required": False, "description": "Display name (ElevenLabs fallback)"}}, "response_example": "(binary MP3 audio)"},
                ],
            },
            # ── Custom Voice Cloning ───────────────────────────
            {
                "section": "Custom Voice Cloning",
                "endpoints": [
                    {"method": "GET", "path": "/api/custom-voices", "summary": "List custom voices", "description": "Returns all custom cloned voices, optionally filtered by provider. Status: pending, processing, ready, or failed.", "curl": f'curl -s "{base}/api/custom-voices?provider=kokoro"', "params": [{"name": "provider", "type": "string", "required": False, "description": "Filter: kokoro, elevenlabs"}], "body": None, "response_example": '[{"id": 1, "name": "My Voice", "provider": "kokoro", "status": "ready", "provider_voice_id": "custom_1"}]'},
                    {"method": "POST", "path": "/api/custom-voices", "summary": "Clone a voice", "description": "Create a custom voice clone by uploading audio samples (multipart form). Cloning runs in background; poll status endpoint. Providers: kokoro (Kanade offline), elevenlabs (IVC API, max 5).", "curl": f"curl -s -X POST {base}/api/custom-voices -F 'name=My Voice' -F 'provider=kokoro' -F 'personality=cheerful' -F 'gender=female' -F 'audio=@sample.wav'", "body": {"name": {"type": "string", "required": True, "description": "Voice name"}, "provider": {"type": "string", "required": True, "description": "kokoro or elevenlabs"}, "system_prompt": {"type": "string", "required": False, "description": "Voice description"}, "personality": {"type": "string", "required": False, "description": "Personality description"}, "gender": {"type": "string", "required": False, "description": "male or female (default: female)"}, "audio": {"type": "file", "required": True, "description": "Audio files (.wav, .mp3, .m4a, .ogg, .flac, .webm)"}}, "response_example": '{"id": 3, "status": "processing", "name": "My Voice"}'},
                    {"method": "GET", "path": "/api/custom-voices/{voice_id}/status", "summary": "Poll voice cloning status", "description": "Check the processing status of a custom voice clone.", "curl": f'curl -s {base}/api/custom-voices/3/status', "params": [{"name": "voice_id", "type": "int", "required": True, "description": "Custom voice ID"}], "body": None, "response_example": '{"id": 3, "status": "ready", "error_message": ""}'},
                    {"method": "PATCH", "path": "/api/custom-voices/{voice_id}", "summary": "Update a custom voice", "description": "Update a custom voice's personality text.", "curl": f"curl -s -X PATCH {base}/api/custom-voices/3 -H 'Content-Type: application/json' -d '{{\"personality\": \"calm\"}}'", "params": [{"name": "voice_id", "type": "int", "required": True, "description": "Custom voice ID"}], "body": {"personality": {"type": "string", "required": False, "description": "New personality"}}, "response_example": '{"success": true}'},
                    {"method": "DELETE", "path": "/api/custom-voices/{voice_id}", "summary": "Delete a custom voice", "description": "Deletes a custom voice and its audio files. For ElevenLabs, also deletes from their API.", "curl": f'curl -s -X DELETE {base}/api/custom-voices/3', "params": [{"name": "voice_id", "type": "int", "required": True, "description": "Custom voice ID"}], "body": None, "response_example": '{"success": true}'},
                    {"method": "POST", "path": "/api/custom-voices/transcribe", "summary": "Transcribe audio", "description": "Upload an audio file and get its transcription. Uses OpenAI Whisper API or local whisper.", "curl": f'curl -s -X POST {base}/api/custom-voices/transcribe -F "audio=@recording.wav"', "body": None, "response_example": '{"transcript": "Hello, this is a sample recording."}'},
                ],
            },
            # ── Skills ───────────────────────────────────────
            {
                "section": "Skills",
                "endpoints": [
                    {"method": "GET", "path": "/api/skills", "summary": "List all skills", "description": "Returns the skills registry.", "curl": f'curl -s {base}/api/skills', "body": None, "response_example": '[{"id": "brainstorming", "name": "brainstorming", "description": "..."}]'},
                    {"method": "GET", "path": "/api/skills/{{skill_id}}", "summary": "Get skill detail", "description": "Returns the full SKILL.md content for a skill.", "curl": f'curl -s {base}/api/skills/brainstorming', "params": [{"name": "skill_id", "type": "str", "required": True, "description": "Skill ID"}], "body": None, "response_example": '{"id": "brainstorming", "content": "..."}'},
                    {"method": "POST", "path": "/api/skills/{{skill_id}}/push", "summary": "Push skill to project", "description": "Copies SKILL.md into project/.pi/skills/<id>/ (works while Pi is cold). Optional instructions become USER_INTENT.md beside SKILL.md.", "curl": f"curl -s -X POST {base}/api/skills/brainstorming/push -H 'Content-Type: application/json' -d '{{\"project_path\": \"/path/to/repo\", \"instructions\": \"audit auth\"}}'", "body": {"project_path": {"type": "str", "required": False, "description": "Project folder (must match DB project folder_location)"}, "instructions": {"type": "str", "required": False, "description": "Saved as USER_INTENT.md next to SKILL.md"}}, "response_example": '{"success": true, "message": "...", "user_intent_file": ".../USER_INTENT.md"}'},
                    {"method": "POST", "path": "/api/skills/{{skill_id}}/spoken-overview", "summary": "Spoken AI overview of a skill", "description": "Uses the configured coding/conversational LLM to summarize the skill for speech, then returns plain-text overview plus base64-encoded MP3 (General playback speed). For the Skills page listen button.", "curl": f"curl -s -X POST {base}/api/skills/brainstorming/spoken-overview -H 'Content-Type: application/json' -d '{{}}'", "body": None, "response_example": '{"overview": "This skill helps you…", "audio_mp3_base64": "..."}'},
                ],
            },
            # ── Settings ───────────────────────────────────────
            {
                "section": "Settings",
                "endpoints": [
                    {"method": "GET", "path": "/api/general", "summary": "Get general settings", "description": "Returns all general settings: voice provider, oracle skin/size/position, playback speed, volume, and UI preferences.", "curl": f'curl -s {base}/api/general', "body": None, "response_example": '{"voice_provider": "kokoro", "kokoro_voice": "af_heart", "playback_speed": 1.0, "selected_oracle": "0.gif", "sphere_size": 9}'},
                    {"method": "POST", "path": "/api/general", "summary": "Save general settings", "description": "Saves all general settings and emits live-update signals for oracle, voice, and playback changes.", "curl": f"curl -s -X POST {base}/api/general -H 'Content-Type: application/json' -d '{{\"voice_provider\": \"kokoro\", \"playback_speed\": 1.2, \"selected_oracle\": \"0.gif\"}}'", "body": {"voice_provider": {"type": "string", "required": False, "description": "kokoro, elevenlabs, openai, coqui"}, "playback_speed": {"type": "float", "required": False, "description": "0.5-2.0"}, "speech_volume": {"type": "int", "required": False, "description": "0-100"}, "selected_oracle": {"type": "string", "required": False, "description": "Oracle skin filename"}, "sphere_size": {"type": "int", "required": False, "description": "Oracle size 4-10"}, "oracle_position": {"type": "string", "required": False, "description": "custom, top_left, top_right, etc."}}, "response_example": '{"success": true, "message": "Settings saved and oracle updated"}'},
                    {"method": "POST", "path": "/api/voice/playback-speed", "summary": "Update playback speed", "description": "Live-update TTS playback speed.", "curl": f"curl -s -X POST {base}/api/voice/playback-speed -H 'Content-Type: application/json' -d '{{\"playback_speed\": 1.5}}'", "body": {"playback_speed": {"type": "float", "required": True, "description": "0.5-2.0"}}, "response_example": '{"success": true, "playback_speed": 1.5}'},
                    {"method": "POST", "path": "/api/oracle/skin", "summary": "Change oracle skin", "description": "Live-update the oracle avatar skin.", "curl": f"curl -s -X POST {base}/api/oracle/skin -H 'Content-Type: application/json' -d '{{\"selected_oracle\": \"3.gif\"}}'", "body": {"selected_oracle": {"type": "string", "required": True, "description": "Skin filename"}}, "response_example": '{"success": true, "selected_oracle": "3.gif"}'},
                    {"method": "GET", "path": "/api/oracle/skins", "summary": "List available oracle skins", "description": "Returns all available oracle avatar skins.", "curl": f'curl -s {base}/api/oracle/skins', "body": None, "response_example": '{"skins": [{"filename": "0.gif", "display_name": "Skin 0"}]}'},
                    {"method": "GET", "path": "/api/thirdparty", "summary": "Get third-party API keys", "description": "Returns third-party provider settings with keys redacted (masked).", "curl": f'curl -s {base}/api/thirdparty', "body": None, "response_example": '{"ollama_url": "http://localhost:11434/", "openai_enabled": true, "openai_key": "sk-****abcd"}'},
                    {"method": "POST", "path": "/api/thirdparty", "summary": "Save third-party API keys", "description": "Saves API keys for all providers. Send the masked value back to keep existing keys unchanged. Triggers agent reload.", "curl": f"curl -s -X POST {base}/api/thirdparty -H 'Content-Type: application/json' -d '{{\"openai_enabled\": true, \"openai_key\": \"sk-proj-...\"}}'", "body": {"ollama_url": {"type": "string", "required": False, "description": "Ollama server URL"}, "openai_enabled": {"type": "bool", "required": False, "description": "Enable OpenAI"}, "openai_key": {"type": "string", "required": False, "description": "OpenAI API key"}, "elevenlabs_enabled": {"type": "bool", "required": False, "description": "Enable ElevenLabs"}, "elevenlabs_key": {"type": "string", "required": False, "description": "ElevenLabs API key"}}, "response_example": '{"success": true, "message": "Settings saved successfully"}'},
                    {"method": "POST", "path": "/api/validate", "summary": "Validate an API key", "description": "Tests whether an API key is valid for a given provider.", "curl": f"curl -s -X POST {base}/api/validate -H 'Content-Type: application/json' -d '{{\"provider\": \"openai\", \"key\": \"sk-proj-...\"}}'", "body": {"provider": {"type": "string", "required": True, "description": "Provider: openai, anthropic, elevenlabs, groq, openrouter, assemblyai"}, "key": {"type": "string", "required": True, "description": "API key to validate"}}, "response_example": '{"valid": true, "error": ""}'},
                ],
            },
            # ── Audio Devices ──────────────────────────────────
            {
                "section": "Audio Devices",
                "endpoints": [
                    {"method": "GET", "path": "/api/audio", "summary": "Get audio settings", "description": "Returns current input/output device selections and lock preferences.", "curl": f'curl -s {base}/api/audio', "body": None, "response_example": '{"input_device": "System Default", "output_device": "MacBook Pro Speakers", "remember_audio_settings": true}'},
                    {"method": "POST", "path": "/api/audio", "summary": "Save audio settings", "description": "Saves input/output device selections. Emits signal for live hot-swap.", "curl": f"curl -s -X POST {base}/api/audio -H 'Content-Type: application/json' -d '{{\"input_device\": \"System Default\", \"output_device\": \"AirPods Pro\"}}'", "body": {"input_device": {"type": "string", "required": True, "description": "Input device name"}, "output_device": {"type": "string", "required": True, "description": "Output device name"}, "remember_audio_settings": {"type": "bool", "required": False, "description": "Lock selections across restarts"}}, "response_example": '{"success": true, "message": "Audio settings saved"}'},
                    {"method": "GET", "path": "/api/audio/devices", "summary": "List audio devices", "description": "Returns available input and output audio devices detected on the system.", "curl": f'curl -s {base}/api/audio/devices', "body": None, "response_example": '{"input_devices": [{"name": "System Default", "id": -1}], "output_devices": [...]}'},
                    {"method": "POST", "path": "/api/audio/detect", "summary": "Detect new audio devices", "description": "Runs a full device scan and merges newly found devices into the known list.", "curl": f'curl -s -X POST {base}/api/audio/detect', "body": None, "response_example": '{"input_devices": [...], "output_devices": [...], "success": true}'},
                ],
            },
            # ── Integrations ──────────────────────────────────
            {
                "section": "Integrations",
                "endpoints": [
                    {"method": "POST", "path": "/api/advanced/telegram/request", "summary": "Request Telegram connection", "description": "Generates a short code and QR code for linking a Telegram account.", "curl": f'curl -s -X POST {base}/api/advanced/telegram/request', "body": None, "response_example": '{"short_code": "ABC123", "qr_url": "https://t.me/..."}'},
                    {"method": "POST", "path": "/api/advanced/telegram/status", "summary": "Check Telegram connection status", "description": "Polls whether the Telegram account has been linked.", "curl": f'curl -s -X POST {base}/api/advanced/telegram/status', "body": None, "response_example": '{"connected": true, "telegram_user_id": 123456}'},
                    {"method": "GET", "path": "/api/advanced/google/oauth-url", "summary": "Get Google OAuth URL", "description": "Returns the Google OAuth authorization URL.", "curl": f'curl -s {base}/api/advanced/google/oauth-url', "body": None, "response_example": '{"url": "https://accounts.google.com/o/oauth2/..."}'},
                    {"method": "GET", "path": "/api/advanced/trello/auth-url", "summary": "Get Trello auth URL", "description": "Returns the Trello authorization URL.", "curl": f'curl -s {base}/api/advanced/trello/auth-url', "body": None, "response_example": '{"url": "https://trello.com/1/authorize?..."}'},
                    {"method": "GET", "path": "/api/advanced/connection-status", "summary": "Get all integration statuses", "description": "Returns connection status for Telegram, Google, Trello, and Jira.", "curl": f'curl -s {base}/api/advanced/connection-status', "body": None, "response_example": '{"telegram": {"connected": true}, "google": {"connected": false}}'},
                    {"method": "GET", "path": "/api/advanced/accounts", "summary": "List connected accounts", "description": "Returns all connected third-party accounts.", "curl": f'curl -s {base}/api/advanced/accounts', "body": None, "response_example": '[{"type": "trello", "name": "My Trello"}]'},
                ],
            },
            # ── System ─────────────────────────────────────────
            {
                "section": "System",
                "endpoints": [
                    {"method": "GET", "path": "/health", "summary": "Health check", "description": "Returns server health status.", "curl": f'curl -s {base}/health', "body": None, "response_example": '{"status": "ok", "services": ["flow", "board", "settings", "chat"]}'},
                    {"method": "GET", "path": "/api/logs", "summary": "Get application logs", "description": "Returns recent application log entries for debugging.", "curl": f'curl -s {base}/api/logs', "body": None, "response_example": '{"logs": "2025-03-24 10:00:00 INFO Agent started..."}'},
                ],
            },
        ]
        return JSONResponse({"sections": endpoints})

    return router
