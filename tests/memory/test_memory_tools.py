"""R10 agent tools for AGENT/USER/MEMORY/EVENTS files."""

from __future__ import annotations

import pytest

import distr.core.memory.files as memory_files
from distr.core.agent.tools.system.memory_tools import (
    MemoryAddTool,
    MemoryEditTool,
    MemoryReadTool,
    MemorySearchTool,
    _normalize_file_key,
)


@pytest.fixture
def mem_root(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_files, "default_memory_dir", lambda: tmp_path)
    memory_files.ensure_memory_files()
    return tmp_path


def test_normalize_file_key() -> None:
    assert _normalize_file_key("MEMORY") == "memory"
    with pytest.raises(ValueError):
        _normalize_file_key("nosuch")


def test_memory_search_ranks_sections(mem_root) -> None:
    mp = memory_files.memory_paths()
    mp["memory"].write_text(
        "# Long-term memory\n\n---\nalpha beta project\n---\ngamma delta hobby\n",
        encoding="utf-8",
    )
    tool = MemorySearchTool()
    out = tool._run(query="gamma hobby", top_k=1)
    assert "gamma" in out.lower()
    assert "hobby" in out.lower()


def test_memory_read_lines(mem_root) -> None:
    mp = memory_files.memory_paths()
    mp["user"].write_text("L1\nL2\nL3\n", encoding="utf-8")
    tool = MemoryReadTool()
    out = tool._run(file="user", start_line=2, end_line=2)
    assert "L2" in out
    body = out.split("\n\n", 1)[-1]
    assert "L2" in body
    assert "L3" not in body


def test_memory_add_appends_memory(mem_root) -> None:
    tool = MemoryAddTool()
    msg = tool._run(section="Note", content="Remember the milk.", target="memory")
    assert "Appended" in msg
    text = memory_files.memory_paths()["memory"].read_text(encoding="utf-8")
    assert "milk" in text


def test_memory_edit_rejects_memory_replace(mem_root) -> None:
    tool = MemoryEditTool()
    out = tool._run(
        file="memory",
        section_header="X",
        new_content="Y",
        mode="replace",
    )
    assert "append-only" in out.lower()


def test_memory_edit_append_memory(mem_root) -> None:
    tool = MemoryEditTool()
    out = tool._run(
        file="memory",
        section_header="Tag",
        new_content="Body text",
        mode="append",
    )
    assert "Appended" in out
    assert "Body text" in memory_files.memory_paths()["memory"].read_text()
