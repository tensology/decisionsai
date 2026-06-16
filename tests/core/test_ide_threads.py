from __future__ import annotations

from unittest.mock import patch

from distr.core.ide_threads.service import format_ide_thread_result, ide_thread_action


def test_ide_thread_list_codex(monkeypatch):
    monkeypatch.setattr(
        "distr.core.ide_threads.codex_adapter.list_threads",
        lambda **kwargs: [{"surface": "codex", "thread_id": "abc", "title": "Fix login"}],
    )
    result = ide_thread_action(action="list", surface="codex")
    assert result["success"] is True
    assert result["threads"][0]["thread_id"] == "abc"
    voice = format_ide_thread_result(result)
    assert "Fix login" in voice
    assert "REFERENCE:" in voice


def test_ide_thread_prompt_codex_builds_resume(monkeypatch):
    calls = []

    def _fake_prompt(**kwargs):
        calls.append(kwargs)
        return {"success": True, "surface": "codex", "output_preview": "Done."}

    monkeypatch.setattr("distr.core.ide_threads.codex_adapter.prompt_thread", _fake_prompt)
    monkeypatch.setattr("distr.core.ide_threads.service._record_prompt_session", lambda **kwargs: None)

    result = ide_thread_action(
        action="prompt",
        surface="codex",
        instruction="Add tests",
        thread_id="thread-123",
        cwd="/tmp/project",
    )
    assert result["success"] is True
    assert calls[0]["thread_id"] == "thread-123"
    assert calls[0]["resume"] is True


def test_ide_thread_amend_requires_codex_thread_id(monkeypatch):
    monkeypatch.setattr(
        "distr.core.ide_threads.codex_adapter.amend_thread",
        lambda **kwargs: {"success": False, "error": "thread_id is required to amend a Codex thread"},
    )
    result = ide_thread_action(action="amend", surface="codex", amendment="Also cover edge cases")
    assert result["success"] is False


def test_ide_thread_read_cursor_uses_bridge(monkeypatch):
    monkeypatch.setattr(
        "distr.core.ide_threads.cursor_adapter.read_thread",
        lambda **kwargs: {
            "surface": "cursor",
            "found": True,
            "messages": [{"role": "assistant", "content": "Implemented the fix."}],
        },
    )
    result = ide_thread_action(action="read", surface="cursor", session_id=42)
    assert result["found"] is True
    assert "Implemented the fix" in format_ide_thread_result(result)


def test_ide_thread_list_cursor_includes_local_transcripts(monkeypatch):
    monkeypatch.setattr(
        "distr.core.ide_threads.cursor_adapter.list_threads",
        lambda **kwargs: [
            {
                "surface": "cursor",
                "thread_id": "chat-1",
                "title": "Fix dictation",
                "source": "cursor_transcript",
            }
        ],
    )
    result = ide_thread_action(action="list", surface="cursor", cwd="/repo/app")
    assert result["success"] is True
    assert result["threads"][0]["source"] == "cursor_transcript"
