"""Project A UI feedback becomes durable standards available to Project B."""

from __future__ import annotations

import contextlib

import distr.core.db.orchestrator  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.orchestrator import OrchestratorUserMemory


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_project_a_ui_feedback_is_recalled_as_project_b_standard(monkeypatch, tmp_path):
    from distr.core.orchestrator import record_ui_feedback_label
    from distr.core.workflow.standards_memory import build_standards_context

    db_path = tmp_path / "cross-project-memory.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", lambda: _session_ctx(factory))

    feedback = "Keep interface controls dense, avoid excessive vertical padding, and show status beside the affected item."
    record_ui_feedback_label(
        label="spacing off",
        reason=feedback,
        board_id=101,
        project_id=201,
    )

    # A new session and unrelated board/project simulate a later project run.
    context_for_project_b = build_standards_context("Project B ticket rules.", board_id=202)

    assert "[GLOBAL USER STANDARDS]" in context_for_project_b
    assert "controls dense" in context_for_project_b
    assert "excessive vertical padding" in context_for_project_b

    backend_context = build_standards_context(
        "Project B backend ticket rules.",
        board_id=202,
        include_ui_standards=False,
    )
    assert "controls dense" not in backend_context
    assert "excessive vertical padding" not in backend_context
    assert "[VISUAL TASTE MEMORY]" not in backend_context

    with _session_ctx(factory) as session:
        memory = session.query(OrchestratorUserMemory).filter_by(category="ui_design_standard").one()
        assert memory.scope == "global"
        assert memory.project_id == 201
        assert memory.enabled == 1


def test_repeated_ui_feedback_reinforces_one_global_principle(monkeypatch, tmp_path):
    from distr.core.orchestrator import record_ui_feedback_label
    from distr.core.workflow.standards_memory import build_global_user_standards_context

    db_path = tmp_path / "reinforced-memory.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", lambda: _session_ctx(factory))

    reason = "Always validate the complete responsive flow in Playwright before calling an interface finished."
    for project_id in (301, 302):
        record_ui_feedback_label(
            label="flow bad",
            reason=reason,
            board_id=project_id,
            project_id=project_id,
        )

    with _session_ctx(factory) as session:
        memories = session.query(OrchestratorUserMemory).filter_by(category="ui_design_standard").all()
        assert len(memories) == 1
        assert memories[0].evidence_count == 2

    context = build_global_user_standards_context()
    assert "reinforced 2x" in context
    assert "responsive flow" in context


def test_project_specific_request_is_not_promoted_as_a_global_standard(monkeypatch):
    from distr.core.workflow.standards_memory import build_global_user_standards_context

    monkeypatch.setattr(
        "distr.core.orchestrator_memory.list_user_memories",
        lambda **_kwargs: [
            {
                "scope": "global",
                "category": "quality_standard",
                "content": (
                    "Use these exact supplied URLs for ticket PLAYER1-177: "
                    "https://example.test/artist"
                ),
                "evidence_count": 4,
            },
            {
                "scope": "global",
                "category": "quality_standard",
                "content": "Always keep raw production secrets out of diagnostic output.",
                "evidence_count": 2,
            },
        ],
    )

    context = build_global_user_standards_context()

    assert "PLAYER1-177" not in context
    assert "example.test" not in context
    assert "raw production secrets" in context


def test_project_b_ui_gate_requires_assessment_against_recalled_standards(monkeypatch, tmp_path):
    from distr.core.orchestrator import list_validation_records, record_ui_feedback_label, record_ui_quality_validation
    from distr.core.workflow.standards_memory import build_standards_context

    db_path = tmp_path / "cross-project-assessment.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    monkeypatch.setattr("distr.core.orchestrator.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.orchestrator_memory.get_session", lambda: _session_ctx(factory))
    monkeypatch.setattr("distr.core.workflow.standards_memory.get_session", lambda: _session_ctx(factory))

    record_ui_feedback_label(
        label="hierarchy unclear",
        reason="Keep the primary action visually dominant and show validation status beside the affected item.",
        board_id=401,
        project_id=501,
    )
    standards = build_standards_context(board_id=402)
    artifacts = {
        "before_unavailable_reason": "New project",
        "after_screenshot": "/tmp/project-b-after.png",
        "flow_summary": "Opened the editor and completed the primary action.",
        "happy_path_steps": ["Open", "Edit", "Save"],
        "click_count": 3,
        "layout_hierarchy_notes": "Primary action is dominant.",
    }

    failed_id = record_ui_quality_validation(
        artifacts=artifacts,
        workflow_id=2,
        run_id=2,
        board_id=402,
        project_id=502,
        standards_context=standards,
    )
    passed_id = record_ui_quality_validation(
        artifacts={
            **artifacts,
            "standards_assessment": (
                "Checked the learned hierarchy standard: the primary action is dominant and status is adjacent."
            ),
        },
        workflow_id=2,
        run_id=3,
        board_id=402,
        project_id=502,
        standards_context=standards,
    )

    validations = {row["id"]: row for row in list_validation_records(limit=20)}
    assert validations[failed_id]["verdict"] == "fail"
    assert "standards_assessment" in validations[failed_id]["payload"]["ui_quality"]["missing"]
    assert validations[passed_id]["verdict"] == "pass"
