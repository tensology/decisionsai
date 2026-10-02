"""Per-user per-skill judgment events + propose-only skill updates."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from distr.core.workflow import skill_judgment_memory as sjm


def _isolated(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skill_judgment.db'}")
    monkeypatch.setattr(sjm, "engine", engine)
    sjm.ensure_tables()
    return engine


def test_negative_judgment_records_event_and_pending_proposal(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    out = sjm.record_skill_judgment_event(
        board_id=7,
        project_id=3,
        skill_id="frontend-design",
        skill_name="Frontend Design",
        skill_ids=["frontend-design", "webapp-testing"],
        harness_category="frontend",
        execution_lane="workflow",
        judgment_label="this is terrible, and this is why",
        feedback_text="this is terrible, and this is why: buttons overlap on mobile",
        lesson_summary="Do not ship overlapping mobile buttons.",
        run_id=12,
        ticket_id=46,
    )
    assert out["event_id"]
    assert out["skill_id"] == "frontend-design"
    assert out["proposal"] and out["proposal"]["status"] == "pending"
    assert out["proposal"]["applied"] is False

    with engine.connect() as conn:
        events = conn.execute(text("SELECT COUNT(*) FROM skill_judgment_events")).scalar_one()
        proposals = conn.execute(text(
            "SELECT status, skill_id, evidence_count FROM skill_judgment_proposals"
        )).mappings().all()
    assert events == 1
    assert len(proposals) == 1
    assert proposals[0]["status"] == "pending"
    assert proposals[0]["skill_id"] == "frontend-design"
    assert proposals[0]["evidence_count"] == 1


def test_positive_judgment_is_lighter_and_does_not_propose(monkeypatch, tmp_path):
    engine = _isolated(monkeypatch, tmp_path)
    out = sjm.record_skill_judgment_event(
        board_id=7,
        skill_id="frontend-design",
        judgment_label="looks good",
        feedback_text="looks good",
        lesson_summary="Passed.",
    )
    assert out["judgment_label"] == "looks good"
    assert out["proposal"] is None
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM skill_judgment_proposals")).scalar_one() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM skill_judgment_events")).scalar_one() == 1


def test_matching_filters_ui_lesson_away_from_backend_skill(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    sjm.record_skill_judgment_event(
        board_id=7,
        skill_id="frontend-design",
        harness_category="frontend",
        judgment_label="redo",
        feedback_text="redo",
        lesson_summary="UI: fix spacing before verify.",
    )
    sjm.record_skill_judgment_event(
        board_id=7,
        skill_id="systematic-debugging",
        harness_category="api",
        judgment_label="redo",
        feedback_text="redo",
        lesson_summary="API: add regression test for null payload.",
    )
    ui = sjm.matching_skill_judgment_lessons(
        board_id=7, skill_id="frontend-design", limit=10,
    )
    api = sjm.matching_skill_judgment_lessons(
        board_id=7, skill_id="systematic-debugging", limit=10,
    )
    assert len(ui) == 1 and "UI:" in ui[0]["lesson_summary"]
    assert len(api) == 1 and "API:" in api[0]["lesson_summary"]
    other = sjm.matching_skill_judgment_lessons(board_id=7, skill_id="ponytail", limit=10)
    assert other == []


def test_proposals_are_board_scoped_not_global(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    sjm.record_skill_judgment_event(
        board_id=7,
        skill_id="frontend-design",
        judgment_label="redo",
        feedback_text="redo",
        lesson_summary="Board 7 lesson",
    )
    sjm.record_skill_judgment_event(
        board_id=99,
        skill_id="frontend-design",
        judgment_label="redo",
        feedback_text="redo",
        lesson_summary="Board 99 lesson",
    )
    board7 = sjm.list_skill_judgment_events(board_id=7, skill_id="frontend-design")
    board99 = sjm.list_skill_judgment_events(board_id=99, skill_id="frontend-design")
    assert len(board7) == 1 and board7[0]["lesson_summary"] == "Board 7 lesson"
    assert len(board99) == 1 and board99[0]["lesson_summary"] == "Board 99 lesson"
    assert sjm.list_skill_judgment_proposals(board_id=7)[0]["user_key"] == "board:7"
    assert sjm.list_skill_judgment_proposals(board_id=99)[0]["user_key"] == "board:99"


def test_approve_proposal_does_not_rewrite_skill(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    out = sjm.record_skill_judgment_event(
        board_id=7,
        skill_id="frontend-design",
        judgment_label="redo",
        feedback_text="redo",
        lesson_summary="Tighten mobile spacing rules.",
    )
    proposal_id = out["proposal"]["proposal_id"]
    decided = sjm.decide_skill_judgment_proposal(proposal_id, approve=True, note="ok later")
    assert decided["ok"] is True
    assert decided["status"] == "approved"
    assert decided["applied"] is False
    assert "not modified" in decided["note"].lower()
    rows = sjm.list_skill_judgment_proposals(board_id=7, status="approved")
    assert len(rows) == 1


def test_resolve_context_from_explicit_and_run_data(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)

    class _Run:
        run_data = '{"turn_skill_ids": ["webapp-testing"], "execution_lane": "cli"}'

    class _DB:
        def query(self, *_a, **_k):
            return self

        def filter(self, *_a, **_k):
            return self

        def first(self):
            return _Run()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(sjm, "get_session", lambda: _DB())
    ctx = sjm.resolve_judgment_skill_context(run_id=5, ticket_title="fix react checkout css")
    assert ctx["skill_id"] == "webapp-testing"
    assert ctx["execution_lane"] == "cli"

    explicit = sjm.resolve_judgment_skill_context(
        skill_id="systematic-debugging",
        skill_ids=["systematic-debugging"],
        harness_category="api",
        execution_lane="workflow",
    )
    assert explicit["skill_id"] == "systematic-debugging"
    assert explicit["harness_category"] == "api"
