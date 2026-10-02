"""Wing/room mapping from Decisions memory surfaces → MemPalace taxonomy.

MemPalace model: wing (project/person) → room (topic) → drawer (verbatim).

Decisions uses a single local palace under ``~/.decisions/mempalace/palace``
with wing ``decisions`` (runtime dual-writes) plus wing ``seed_corpus_pack``
(bundled curated pack, first-run mined; default RAG source).
"""

from __future__ import annotations

from typing import Final

# Default wing for cross-cutting Decisions memory
WING_DECISIONS: Final = "decisions"
# Bundled seed corpus (first-run mined; read-heavy RAG source)
WING_SEED_CORPUS_PACK: Final = "seed_corpus_pack"

# Rooms (one per legacy surface)
ROOM_MARKDOWN_MEMORY: Final = "markdown_memory"  # AGENT/USER/MEMORY/EVENTS.md
ROOM_LEARNINGS: Final = "learnings"              # .decisions/learnings JSONL
ROOM_ORCHESTRATOR: Final = "orchestrator"        # OrchestratorUserMemory SQL
ROOM_WORKSPACE: Final = "workspace"              # ~/.decisions/workspaces/*
ROOM_STEERING: Final = "steering"                # run steering_log + standards
ROOM_WORKFLOW: Final = "workflow"                # workflow handoffs / ledgers
ROOM_RAG: Final = "rag"                          # project/doc retrieval (replaces LlamaIndex query path)
ROOM_TICKET_CLI: Final = "ticket_cli"            # ticket CLI enrichment briefs
ROOM_HEADROOM_META: Final = "headroom_meta"      # optional pointers only — not blob store

# File-key → room+tag for markdown memory files
MARKDOWN_FILE_ROOMS: Final = {
    "agent": ROOM_MARKDOWN_MEMORY,
    "user": ROOM_MARKDOWN_MEMORY,
    "memory": ROOM_MARKDOWN_MEMORY,
    "events": ROOM_MARKDOWN_MEMORY,
}

SURFACE_LABELS: Final = {
    ROOM_MARKDOWN_MEMORY: "models/memory markdown (R8)",
    ROOM_LEARNINGS: "learnings-keeper JSONL",
    ROOM_ORCHESTRATOR: "orchestrator SQL user memory",
    ROOM_WORKSPACE: "workspace_memory companion FS",
    ROOM_STEERING: "steering_memory / standards",
    ROOM_WORKFLOW: "workflow ledgers / handoffs",
    ROOM_RAG: "LlamaIndex RAG query path",
    ROOM_TICKET_CLI: "ticket_cli_memory enrichment",
    ROOM_HEADROOM_META: "Headroom MCP (meta only)",
}
