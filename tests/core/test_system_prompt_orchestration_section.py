"""Ensure the voice agent system prompt includes orchestration routing guidance."""

from pathlib import Path


def test_system_prompt_contains_orchestration_routing():
    template_path = Path(__file__).resolve().parents[2] / "distr" / "core" / "agent" / "services" / "llm" / "system_prompt.txt"
    text = template_path.read_text(encoding="utf-8")
    assert "ORCHESTRATION ROUTING" in text
    assert "create_ticket" in text
    assert "pi_agent" in text
    assert "terminal_overview" in text
    assert "codex_thread_context" in text
    assert "automatically gathers the recent conversation thread" in text
    assert "find_skill" in text
    assert "push_skill" in text
    assert "list_workflows" in text or "run_workflow" in text
    assert "MULTI-ACTION TOOL EXECUTION" in text
    assert "ordered action queue" in text
    assert "verify material tool results" in text.lower()
