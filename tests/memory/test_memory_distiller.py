"""R9 memory distillation — file safety and injectable LLM."""

from __future__ import annotations

from pathlib import Path

import pytest

from distr.core.events import MEMORY_DISTILLED, get_event_bus, reset_event_bus_for_tests
from distr.core.memory.distiller import DistillOutcome, events_effectively_empty, run_memory_distillation
from distr.core.memory.files import (
    DEFAULT_EVENTS_BODY,
    append_events_text,
    ensure_memory_files,
    memory_paths,
)


def test_events_effectively_empty_default_only() -> None:
    assert events_effectively_empty(DEFAULT_EVENTS_BODY)
    assert events_effectively_empty(DEFAULT_EVENTS_BODY + "\n\n")


def test_events_effectively_empty_has_user_content() -> None:
    raw = DEFAULT_EVENTS_BODY + "\n\nUser mentioned the Q3 roadmap.\n"
    assert not events_effectively_empty(raw)


def test_run_skipped_when_no_events(tmp_path: Path) -> None:
    ensure_memory_files(root=tmp_path)
    outcome = run_memory_distillation(
        root=tmp_path,
        settings={},
        llm_distill=lambda s: "should not run",
    )
    assert isinstance(outcome, DistillOutcome)
    assert outcome.ok and outcome.skipped


def test_run_distills_appends_memory_clears_events(tmp_path: Path) -> None:
    reset_event_bus_for_tests()
    seen: list = []
    bus = get_event_bus()
    bus.subscribe(MEMORY_DISTILLED, lambda _t, d: seen.append(d))

    ensure_memory_files(root=tmp_path)
    append_events_text("Discussed budget with Alex.", root=tmp_path)

    outcome = run_memory_distillation(
        root=tmp_path,
        settings={},
        llm_distill=lambda s: "Budget discussions with Alex are recurring.\n---\nFiscal year focus.",
    )
    assert outcome.ok and not outcome.skipped
    mem_text = memory_paths(tmp_path)["memory"].read_text(encoding="utf-8")
    assert "Alex" in mem_text
    events_text = memory_paths(tmp_path)["events"].read_text(encoding="utf-8")
    assert "Discussed budget" not in events_text
    assert "Events log" in events_text
    backups = list((tmp_path / "distill_backups").glob("EVENTS_*.md"))
    assert len(backups) == 1
    assert "Discussed budget" in backups[0].read_text(encoding="utf-8")
    assert len(seen) == 1
    assert "backup_path" in seen[0]


def test_run_llm_failure_preserves_events(tmp_path: Path) -> None:
    ensure_memory_files(root=tmp_path)
    append_events_text("Do not lose this.", root=tmp_path)
    paths = memory_paths(tmp_path)
    before = paths["events"].read_text(encoding="utf-8")

    def boom(_: str) -> str:
        raise RuntimeError("LLM unavailable")

    outcome = run_memory_distillation(
        root=tmp_path,
        settings={},
        llm_distill=boom,
    )
    assert not outcome.ok
    assert paths["events"].read_text(encoding="utf-8") == before
