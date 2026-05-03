"""Tests for R8 memory files (caps, atomic writes, context snippets)."""

from __future__ import annotations

from pathlib import Path

from distr.core.memory import files


def test_ensure_memory_files_creates_four(tmp_path: Path) -> None:
    base = files.ensure_memory_files(root=tmp_path, system_prompt_template=None)
    assert base == tmp_path
    for name in ("AGENT.md", "USER.md", "MEMORY.md", "EVENTS.md"):
        assert (tmp_path / name).is_file()


def test_append_section_and_cap(tmp_path: Path) -> None:
    files.ensure_memory_files(root=tmp_path)
    huge = "x" * 600
    for i in range(400):
        files.append_markdown_section("memory", f"sec {i}\n{huge}", root=tmp_path)
    text = files.memory_paths(tmp_path)["memory"].read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) <= files.CAP_MEMORY_BYTES + 128


def test_events_line_prune(tmp_path: Path) -> None:
    files.ensure_memory_files(root=tmp_path)
    line = "x" * 2000
    for _ in range(700):
        files.append_events_text(line, root=tmp_path)
    raw = files.memory_paths(tmp_path)["events"].read_text(encoding="utf-8")
    assert len(raw.encode("utf-8")) <= files.CAP_EVENTS_BYTES + 400


def test_load_context_snippets(tmp_path: Path) -> None:
    files.ensure_memory_files(root=tmp_path)
    files.append_markdown_section("user", "prefer dark mode", root=tmp_path)
    sn = files.load_context_snippets_for_llm(root=tmp_path)
    assert "dark mode" in sn["user"]
    assert "agent" in sn and isinstance(sn["agent"], str)


def test_extract_agent_seed_identity_header() -> None:
    tpl = "# Foo\n\n## Identity\n\nI am TestBot.\n\n## Other\n\nnoise"
    out = files.extract_agent_seed_from_template(tpl)
    assert "TestBot" in out
    assert "noise" not in out


def test_atomic_replace_visible(tmp_path: Path) -> None:
    p = tmp_path / "t.md"
    files.atomic_write_text(p, "one")
    files.atomic_write_text(p, "two")
    assert p.read_text(encoding="utf-8") == "two"
