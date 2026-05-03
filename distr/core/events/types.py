"""Event type identifiers for in-process pub/sub (EventBus).

These constants are the contract for SSE, audit hooks, and cross-module signals.
Handlers receive ``(event_type, data)`` where ``data`` is caller-defined payload.
"""

# Chat
CHAT_MESSAGE_RECEIVED = "chat.message.received"
CHAT_MESSAGE_SENT = "chat.message.sent"

# Initiative
INITIATIVE_ACTION_PROPOSED = "initiative.action.proposed"
INITIATIVE_ACTION_EXECUTED = "initiative.action.executed"
INITIATIVE_DRAFT_CREATED = "initiative.draft.created"
INITIATIVE_DRAFT_APPROVED = "initiative.draft.approved"
INITIATIVE_DRAFT_REJECTED = "initiative.draft.rejected"

# MCP
MCP_SERVER_CONNECTED = "mcp.server.connected"
MCP_SERVER_DISCONNECTED = "mcp.server.disconnected"
MCP_TOOL_CALLED = "mcp.tool.called"

# Workflows
WORKFLOW_STEP_STARTED = "workflow.step.started"
WORKFLOW_STEP_COMPLETED = "workflow.step.completed"

# Memory
MEMORY_DISTILLED = "memory.distilled"
MEMORY_FILE_CHANGED = "memory.file.changed"

# Proactive / scheduler
PROACTIVE_TASK_CREATED = "proactive.task.created"
PROACTIVE_TASK_EXECUTED = "proactive.task.executed"

# Integrations (platform-agnostic)
INTEGRATION_CONNECTED = "integration.connected"
INTEGRATION_DISCONNECTED = "integration.disconnected"

ALL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        CHAT_MESSAGE_RECEIVED,
        CHAT_MESSAGE_SENT,
        INITIATIVE_ACTION_PROPOSED,
        INITIATIVE_ACTION_EXECUTED,
        INITIATIVE_DRAFT_CREATED,
        INITIATIVE_DRAFT_APPROVED,
        INITIATIVE_DRAFT_REJECTED,
        MCP_SERVER_CONNECTED,
        MCP_SERVER_DISCONNECTED,
        MCP_TOOL_CALLED,
        WORKFLOW_STEP_STARTED,
        WORKFLOW_STEP_COMPLETED,
        MEMORY_DISTILLED,
        MEMORY_FILE_CHANGED,
        PROACTIVE_TASK_CREATED,
        PROACTIVE_TASK_EXECUTED,
        INTEGRATION_CONNECTED,
        INTEGRATION_DISCONNECTED,
    }
)
