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
    monkeypatch.setattr("distr.core.ide_threads.lock.remember_lock", lambda **kwargs: None)

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


def test_follow_up_uses_locked_codex_thread_and_keeps_cursor_lock(monkeypatch):
    monkeypatch.setattr(
        "distr.core.ide_threads.lock.resolve_lock",
        lambda **kwargs: {"thread_id": "codex-locked", "folder": "/repo/tensorg", "session_id": None}
        if kwargs.get("surface") == "codex"
        else None,
    )
    remembered = []
    monkeypatch.setattr(
        "distr.core.ide_threads.lock.remember_lock",
        lambda **kwargs: remembered.append(kwargs) or kwargs,
    )
    monkeypatch.setattr("distr.core.ide_threads.service._record_prompt_session", lambda **kwargs: None)

    def _fake_prompt(**kwargs):
        assert kwargs["thread_id"] == "codex-locked"
        assert kwargs["resume"] is True
        return {"success": True, "surface": "codex", "thread_id": "codex-locked", "output_preview": "Done."}

    monkeypatch.setattr("distr.core.ide_threads.codex_adapter.prompt_thread", _fake_prompt)
    result = ide_thread_action(
        action="amend",
        surface="codex",
        amendment="Also cover the empty state",
        project="TensorG",
    )
    assert result["success"] is True
    assert result["locked"] is True
    assert remembered[0]["thread_id"] == "codex-locked"
    assert remembered[0]["surface"] == "codex"


def test_new_thread_does_not_resume_the_lock(monkeypatch):
    monkeypatch.setattr(
        "distr.core.ide_threads.lock.resolve_lock",
        lambda **kwargs: {"thread_id": "old-thread", "folder": "/repo/tensorg"},
    )
    monkeypatch.setattr("distr.core.ide_threads.lock.remember_lock", lambda **kwargs: kwargs)
    monkeypatch.setattr("distr.core.ide_threads.service._record_prompt_session", lambda **kwargs: None)

    def _fake_prompt(**kwargs):
        assert kwargs["thread_id"] == ""
        assert kwargs["resume"] is False
        return {"success": True, "surface": "cursor", "thread_id": "new-cursor-thread", "output_preview": "Hello World"}

    monkeypatch.setattr("distr.core.ide_threads.cursor_adapter.prompt_thread", _fake_prompt)
    result = ide_thread_action(
        action="prompt",
        surface="cursor",
        instruction="Hello World",
        project="TensorG",
        new_thread=True,
    )
    assert result["thread_id"] == "new-cursor-thread"
    assert result["locked"] is True


def test_cli_output_extracts_codex_and_cursor_thread_ids():
    from distr.core.ide_threads.lock import summarize_cli_output

    codex_id, codex_text = summarize_cli_output(
        '\n'.join(
            [
                '{"type":"thread.started","thread_id":"codex-thread-9"}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"Hello World"}}',
            ]
        )
    )
    assert codex_id == "codex-thread-9"
    assert codex_text == "Hello World"

    cursor_id, cursor_text = summarize_cli_output(
        '\n'.join(
            [
                '{"type":"system","subtype":"init","session_id":"cursor-session-9"}',
                '{"type":"result","result":"Hello World"}',
            ]
        )
    )
    assert cursor_id == "cursor-session-9"
    assert cursor_text == "Hello World"


def test_prompt_text_keeps_development_harness_separate_from_ide_lock():
    from distr.core.developer_context import (
        DeveloperRuntimeContext,
        DeveloperWorkContext,
    )

    text = DeveloperWorkContext(
        runtime=DeveloperRuntimeContext(cwd="/repo"),
        active_thread={"chat_id": 4, "title": "Implement login"},
        ide_thread_locks={
            "active_surface": "cursor",
            "by_surface": {
                "cursor": {"thread_id": "cursor-session-9", "project": "TensorG", "folder": "/repo/tensorg"},
                "codex": {"thread_id": "codex-thread-9", "project": "TensorG", "folder": "/repo/tensorg"},
            },
        },
    ).to_prompt_text()
    assert "development_harness_thread: chat=4" in text
    assert "locked_ide_threads" in text
    assert "cursor-session-9" in text
    assert "codex-thread-9" in text
    assert "not the Development harness" in text


def test_development_section_request_does_not_hold_the_ide_tool(monkeypatch):
    from distr.core.ide_threads.lock import hold_ide_thread_for_message

    monkeypatch.setattr(
        "distr.core.ide_threads.lock.load_state",
        lambda **kwargs: {"active_surface": "cursor", "by_surface": {"cursor": {"thread_id": "cursor-session-9"}}},
    )
    assert hold_ide_thread_for_message("okay, also add a test") is True
    assert hold_ide_thread_for_message("run this in the development section") is False
