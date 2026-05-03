"""Cross-chat memory files (R8) — AGENT.md, USER.md, MEMORY.md, EVENTS.md."""

from distr.core.memory.distiller import DistillOutcome, run_memory_distillation
from distr.core.memory.files import (
    append_events_text,
    append_markdown_section,
    atomic_write_text,
    ensure_memory_files,
    load_context_snippets_for_llm,
    memory_paths,
    try_load_system_prompt_template,
)
from distr.core.memory.watcher import MemoryFilesWatcher

__all__ = [
    "DistillOutcome",
    "MemoryFilesWatcher",
    "append_events_text",
    "append_markdown_section",
    "atomic_write_text",
    "ensure_memory_files",
    "load_context_snippets_for_llm",
    "memory_paths",
    "run_memory_distillation",
    "try_load_system_prompt_template",
]
