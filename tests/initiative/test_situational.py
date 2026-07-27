"""Situational spine + handoff resume checks for Initiative betterment."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from distr.core.initiative.context import ContextAssembler, ContextBundle
from distr.core.initiative.situational import (
    LONG_IDLE_SECONDS,
    build_situational,
    format_gap_seconds,
    format_situational_prompt_block,
    handoff_resume_proposal,
    peek_handoff,
    should_prefer_handoff_resume,
    situational_one_liner,
)


def test_format_gap_seconds():
    assert format_gap_seconds(45) == "45s"
    assert format_gap_seconds(5 * 3600 + 12 * 60) == "5h 12m"


def test_peek_handoff_and_build():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mem = root / ".decisions" / "memory"
        mem.mkdir(parents=True)
        (mem / "handoff.md").write_text(
            "# Handoff\n\nWas shipping initiative spine.\n\n_updated: 2026-07-20T20:00:00Z_\n",
            encoding="utf-8",
        )
        peek = peek_handoff(str(root))
        assert "initiative spine" in peek

        fixed = datetime(2026, 7, 20, 22, 0, tzinfo=timezone(timedelta(hours=2)))
        chat_done = (fixed - timedelta(hours=5, minutes=12)).timestamp()
        sit = build_situational(
            active_project={"folder_location": str(root)},
            developer_context={},
            last_chat_stream_at=chat_done,
            last_cycle_at=chat_done,
            now=fixed,
        )
        assert sit["idle_gap"] == "5h 12m"
        assert sit["idle_gap_seconds"] == 5 * 3600 + 12 * 60
        assert "cursor_siblings" not in sit
        assert "initiative spine" in sit["handoff_peek"]
        assert sit["now_local"]
        assert "Situational:" in format_situational_prompt_block(sit)
        assert "prefer resume-from-handoff" in format_situational_prompt_block(sit)
        assert "idle 5h 12m" in situational_one_liner(sit)
        assert should_prefer_handoff_resume(sit)
        raw = handoff_resume_proposal(sit)
        assert raw is not None
        assert raw["payload"]["kind"] == "handoff_resume"
        assert "I noticed we left off" in raw["description"]
        assert "Want me to pick that up" in raw["description"]


def test_short_idle_does_not_prefer_handoff():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mem = root / ".decisions" / "memory"
        mem.mkdir(parents=True)
        (mem / "handoff.md").write_text("# Handoff\n\nStill open.\n", encoding="utf-8")
        fixed = datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc)
        recent = (fixed - timedelta(minutes=20)).timestamp()
        sit = build_situational(
            active_project={"folder_location": str(root)},
            last_chat_stream_at=recent,
            now=fixed,
        )
        assert sit["idle_gap_seconds"] < LONG_IDLE_SECONDS
        assert not should_prefer_handoff_resume(sit)
        assert handoff_resume_proposal(sit) is None


def test_context_bundle_includes_situational_when_build_mocked():
    assembler = ContextAssembler()
    with patch.object(assembler, "_fetch_chat_history", return_value=[]), \
         patch.object(assembler, "_fetch_scheduled_sessions", return_value=[]), \
         patch.object(assembler, "_fetch_kanban_summary", return_value=[]), \
         patch.object(assembler, "_fetch_board_notes", return_value=[{"id": 1}]), \
         patch.object(assembler, "_fetch_stuck_tasks", return_value=[]), \
         patch.object(assembler, "_fetch_unfinished_workflows", return_value=[]), \
         patch.object(assembler, "_fetch_active_project", return_value={}), \
         patch.object(assembler, "_fetch_available_tools", return_value=[]), \
         patch.object(assembler, "_fetch_skills", return_value=[]), \
         patch.object(assembler, "_fetch_recent_audit", return_value=[]), \
         patch.object(assembler, "_fetch_memory_snippets", return_value=("", "", "")), \
         patch("distr.core.developer_context.build_developer_context") as mock_dev, \
         patch("distr.core.initiative.work_scanner.build_work_scan", return_value={}), \
         patch("distr.core.initiative.situational.build_situational", return_value={"now_local": "x"}):
        work = MagicMock()
        work.to_dict.return_value = {"runtime": {"cwd": "/tmp"}}
        work.to_prompt_text.return_value = "- now: stamped"
        mock_dev.return_value = work
        bundle = assembler.build({})
        assert isinstance(bundle, ContextBundle)
        assert bundle.situational == {"now_local": "x"}
        assert bundle.developer_context_text == "- now: stamped"
        assert bundle.board_notes == [{"id": 1}]


def test_handoff_resume_proposal_preferred_shape():
    """Service calls handoff_resume before work_scan; assert the proposal shape."""
    sit = {
        "idle_gap": "5h 12m",
        "idle_gap_seconds": 5 * 3600 + 12 * 60,
        "handoff_peek": "# Handoff\n\nFinish the initiative spine.\n",
        "project_folder": "/tmp/proj",
    }
    raw = handoff_resume_proposal(sit)
    assert raw is not None
    assert raw["action_type"] == "suggestion"
    assert raw["payload"]["kind"] == "handoff_resume"
    # Short idle must not produce a resume proposal even with handoff text
    sit_short = {**sit, "idle_gap_seconds": 60, "idle_gap": "1m"}
    assert handoff_resume_proposal(sit_short) is None
