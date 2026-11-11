"""Period work report: SAST periods, collation across sources, project filter, persistence, PDF.

Also re-checks that the existing Run history and Time log lists still behave.
"""
from __future__ import annotations

import contextlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base, Chat
from distr.core.db import work_reports as _work_reports  # noqa: F401 - register table
from distr.core.db.kanban import ProjectExecutionSession
from distr.core.db.projects import Project
from distr.core.db.workflow import DevelopmentTimeEntry, DevelopmentWorkItem
from distr.core.reports import month
from distr.core.reports.service import list_reports, list_time_entries


# ---------------------------------------------------------------- periods

def test_scheduled_wrapper_is_recognised_but_quoted_schedule_is_not():
    header='Automation control: auto_15. This thread owns that scheduled task.'
    assert month.scheduled_instruction('[DECISIONS DEVELOPMENT THREAD]\n'+header+'\nCurrent user instruction:\nRefresh the local database')
    assert not month.scheduled_instruction('Current user instruction:\nExplain this old run:\n'+header)
    assert not month.scheduled_instruction('Conversation context since the latest checkpoint:\n'+header)
    assert not month.scheduled_instruction('Investigate the automation schedule')


def test_schedule_provenance_survives_a_local_idle_gap():
    start=datetime(2026,11,10,7)
    meta={'cwd':'/example','activity':[
        {'role':'user','time':start,'text':'Automation control: auto_15. This thread owns that scheduled task.','workdirs':[]},
        {'role':'assistant','time':start+timedelta(hours=1),'text':'Database refresh completed.','workdirs':[]}]}
    matcher=month.ProjectMatcher({1:{'name':'Example','folder':'/example'}})
    context=month.session_interval_context(meta,start+timedelta(minutes=50),start+timedelta(hours=1),matcher)
    assert context['scheduled_automation'] is True
    meta['activity'].append({'role':'user','time':start+timedelta(hours=2),'text':'Correct the actual customer billing screen','workdirs':[]})
    context=month.session_interval_context(meta,start+timedelta(hours=2),start+timedelta(hours=2,minutes=5),matcher)
    assert context['scheduled_automation'] is False


def test_bound_automation_thread_timer_is_excluded(report_db):
    with report_db['session']() as db:
        chat=db.query(Chat).filter(Chat.title=='Alpha thread').one()
        chat.params=json.dumps({'development':{'source_type':'automation','source_ref':'auto_22'}})
        db.commit()
    result=month.collate(month.resolve_period(start='2026-10-10',end='2026-10-10'),
                         get_session=report_db['session'],include_local=False)
    assert all(b['source']!='decisions_thread' for b in result['blocks'])


def test_automation_filter_excludes_database_and_local_wrappers(monkeypatch):
    start=datetime(2026,11,10,7)
    rows=[month._block('pi_decisions','pi','Pi',start,start+timedelta(minutes=12),1,'Scheduled refresh','execution:979'),
          month._block('pi_local','pi','Pi',start,start+timedelta(minutes=12),1,'Scheduled refresh','pi_local:schedule'),
          month._block('pi_local','pi','Pi',start+timedelta(hours=1),start+timedelta(hours=1,minutes=20),1,'Corrected billing','pi_local:manual')]
    rows[0]['scheduled_automation']=rows[1]['scheduled_automation']=True
    monkeypatch.setattr(month,'load_projects',lambda *a:{1:{'name':'Example','folder':'/example'}})
    monkeypatch.setattr(month,'collect_database_blocks',lambda *a:([dict(r) for r in rows],{}))
    from distr.core.reports import projection_store
    monkeypatch.setattr(projection_store,'manual_project_map',lambda *a:{})
    monkeypatch.setattr(projection_store,'manual_interval_project_map',lambda *a:{})
    period=month.resolve_period(start='2026-11-10',end='2026-11-10')
    result=month.collate(period,get_session=lambda:None,include_local=False)
    assert [b['ref'] for b in result['blocks']]==['pi_local:manual']
    included=month.collate(period,get_session=lambda:None,include_local=False,include_automations=True)
    assert len(included['blocks'])==3

def test_default_period_is_sixth_of_previous_month_through_today():
    period = month.default_period(date(2026, 11, 6))
    assert (period.start, period.end, period.kind) == (date(2026, 10, 6), date(2026, 11, 6), "rolling")
    january = month.default_period(date(2026, 1, 15))
    assert (january.start, january.end) == (date(2025, 12, 6), date(2026, 1, 15))
    assert month.resolve_period(today=date(2026, 11, 6)) == period


def test_month_period_uses_sast_midnight_boundaries():
    period = month.month_period("2026-10", today=date(2026, 11, 6))
    assert (period.start, period.end, period.kind) == (date(2026, 10, 1), date(2026, 10, 31), "month")
    lo, hi = period.bounds_utc()
    assert lo == datetime(2026, 9, 30, 22, 0)
    assert hi == datetime(2026, 10, 31, 22, 0)
    to_date = month.month_period("2026-11", today=date(2026, 11, 6))
    assert to_date.end == date(2026, 11, 6) and "to date" in to_date.label
    with pytest.raises(ValueError):
        month.month_period("2026-13")


def test_period_options_lead_with_rolling_default_then_months():
    options = month.period_options(date(2026, 11, 6), months=3)
    assert [o["value"] for o in options] == ["rolling", "2026-11", "2026-10", "2026-09"]
    assert options[0]["start"] == "2026-10-06" and options[0]["end"] == "2026-11-06"


def test_parse_project_ids():
    assert month.parse_project_ids(None) is None
    assert month.parse_project_ids("all") is None
    assert month.parse_project_ids("3,1,3") == [1, 3]
    assert month.parse_project_ids([0, 2]) == [0, 2]
    with pytest.raises(ValueError):
        month.parse_project_ids("x")


def test_activity_intervals_split_on_idle_gaps():
    t = lambda h, m: datetime(2026, 10, 10, h, m)  # noqa: E731
    spans = month.activity_intervals([t(8, 0), t(8, 10), t(8, 25), t(11, 0)])
    assert spans[0] == (t(8, 0), t(8, 25))
    assert len(spans) == 1  # an isolated timestamp establishes no elapsed work


def test_split_by_sast_day_cuts_at_local_midnight():
    # 21:00-23:00 UTC = 23:00-01:00 SAST: one hour on each local day.
    parts = month.split_by_sast_day(datetime(2026, 10, 9, 21, 0), datetime(2026, 10, 9, 23, 0))
    assert [(d, (e - s).seconds) for d, s, e in parts] == [("2026-10-09", 3600), ("2026-10-10", 3600)]


# ---------------------------------------------------------------- collation

@pytest.fixture()
def report_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'reports.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def get_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr("distr.core.reports.service.get_session", get_session)
    # tmp_path itself lives under pytest-of-<user>; only the fake "pytest-1" session counts as a test run here.
    monkeypatch.setattr(month, "_is_test_cwd", lambda cwd: "/pytest-1/" in str(cwd or ""))
    folder_a = tmp_path / "alpha"
    folder_b = tmp_path / "beta"
    folder_a.mkdir()
    folder_b.mkdir()
    with get_session() as db:
        alpha = Project(name="Alpha", folder_location=str(folder_a))
        beta = Project(name="Beta", folder_location=str(folder_b))
        db.add_all([alpha, beta])
        db.flush()
        chat_a = Chat(title="Alpha thread", project_id=alpha.id, params="{}")
        chat_loose = Chat(title="Loose thread", project_id=None, params="{}")
        db.add_all([chat_a, chat_loose])
        db.flush()
        item_a = DevelopmentWorkItem(chat_id=chat_a.id, identity_key=f"thread:{chat_a.id}", project_id=alpha.id,
                                     ticket_title="Build checkout")
        item_loose = DevelopmentWorkItem(chat_id=chat_loose.id, identity_key=f"thread:{chat_loose.id}")
        db.add_all([item_a, item_loose])
        db.flush()
        # Times are naive UTC. 2026-10-10 08:00 UTC = 10:00 SAST.
        db.add_all([
            DevelopmentTimeEntry(work_item_id=item_a.id, chat_id=chat_a.id, started_at=datetime(2026, 10, 10, 8, 0),
                                 ended_at=datetime(2026, 10, 10, 9, 0), seconds=3600, source="play"),
            # 22:30 UTC on 31 Oct is 00:30 SAST on 1 Nov: November, not October.
            DevelopmentTimeEntry(work_item_id=item_a.id, chat_id=chat_a.id, started_at=datetime(2026, 10, 31, 22, 30),
                                 ended_at=datetime(2026, 10, 31, 23, 0), seconds=1800, source="play"),
            DevelopmentTimeEntry(work_item_id=item_loose.id, chat_id=chat_loose.id,
                                 started_at=datetime(2026, 10, 12, 8, 0), ended_at=datetime(2026, 10, 12, 8, 30),
                                 seconds=1800, source="auto_pause"),
        ])
        # Pi run inside the Alpha timer interval: overlaps, so Alpha's active time is still one hour.
        db.add(ProjectExecutionSession(project_id=alpha.id, route_backend="pi", route_type="project_cli",
                                       origin="development", status="completed",
                                       started_at=datetime(2026, 10, 10, 8, 15), completed_at=datetime(2026, 10, 10, 8, 45)))
        db.add(ProjectExecutionSession(project_id=beta.id, route_backend="cursor", route_type="project_cli",
                                       origin="workflow", status="completed",
                                       started_at=datetime(2026, 10, 20, 12, 0), completed_at=datetime(2026, 10, 20, 12, 20)))
        # The report's own model run must never count as work.
        db.add(ProjectExecutionSession(project_id=beta.id, route_backend="pi", route_type="project_cli",
                                       origin="development", status="completed",
                                       input_packet=json.dumps({"prompt": month.REPORT_MARKER + " build"}),
                                       started_at=datetime(2026, 10, 21, 12, 0), completed_at=datetime(2026, 10, 21, 13, 0)))
        db.commit()
        ids = {"alpha": alpha.id, "beta": beta.id}

    pi_root = tmp_path / "pi-sessions" / "--beta--"
    pi_root.mkdir(parents=True)
    lines = [
        {"type": "session", "version": 3, "id": "s1", "timestamp": "2026-10-15T06:00:00.000Z", "cwd": str(folder_b / "src")},
        {"type": "message", "id": "m1", "parentId": None, "timestamp": "2026-10-15T06:00:05.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "Fix the beta login bug"}]}},
        {"type": "message", "id": "m1b", "parentId": "m1", "timestamp": "2026-10-15T06:20:00.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "working"}]}},
        {"type": "message", "id": "m2", "parentId": "m1", "timestamp": "2026-10-15T06:40:00.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed the beta login bug and verified sign-in."}]}},
    ]
    (pi_root / "2026-10-15T06-00-00-000Z_s1.jsonl").write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    test_root = tmp_path / "pi-sessions" / "--pytest--"
    test_root.mkdir()
    (test_root / "x.jsonl").write_text(json.dumps({"type": "session", "id": "t", "timestamp": "2026-10-15T06:00:00Z",
                                                    "cwd": "/private/var/folders/T/pytest-of-paul/pytest-1/x"}) + "\n")
    return {"session": get_session, "ids": ids, "pi_root": tmp_path / "pi-sessions",
            "codex_root": tmp_path / "missing-codex"}


def _collate(report_db, projects=None):
    period = month.month_period("2026-10", today=date(2026, 11, 6))
    return month.collate(period, projects, get_session=report_db["session"], pi_root=report_db["pi_root"],
                         codex_root=report_db["codex_root"])


def test_collation_merges_sources_by_project_and_duration(report_db):
    snapshot = _collate(report_db)
    ids = report_db["ids"]
    by_project = {row["project_id"]: row for row in snapshot["by_project"]}
    assert by_project[ids["alpha"]]["seconds"] == 3600  # Pi run inside the timer merged; 1 Nov 00:30 SAST entry excluded
    assert by_project[month.UNASSIGNED_ID]["seconds"] == 1800
    # Beta: 20 min Cursor + 40 min local Pi session (from its cwd); the marked report run is excluded.
    assert by_project[ids["beta"]]["seconds"] == 1200 + 40 * 60
    sources = {row["source"]: row for row in snapshot["sources"]}
    assert sources["pi_decisions"]["blocks"] == 1
    assert sources["cursor_decisions"]["blocks"] == 1
    assert sources["pi_local"]["blocks"] == 1
    assert sources["codex_local"]["state"] == "unavailable"
    assert sources["cursor_local"]["state"] == "not_read"
    local = next(b for b in snapshot["blocks"] if b["source"] == "pi_local")
    assert local["title"] == "Fixed the beta login bug and verified sign-in."  # recorded outcome, not the prompt
    assert local["start_local"] == "2026-10-15T08:00:00"  # SAST
    assert snapshot["totals"]["seconds"] == sum(r["seconds"] for r in snapshot["by_project"])
    assert snapshot["period"]["timezone"] == "Africa/Johannesburg"


def test_month_boundary_excludes_entries_after_sast_midnight(report_db):
    with report_db["session"]() as db:
        entry = db.query(DevelopmentTimeEntry).filter(DevelopmentTimeEntry.started_at == datetime(2026, 10, 31, 22, 30)).one()
        assert entry is not None
    november = month.month_period("2026-11", today=date(2026, 11, 6))
    snapshot = month.collate(november, None, get_session=report_db["session"], pi_root=report_db["pi_root"],
                             codex_root=report_db["codex_root"])
    alpha = report_db["ids"]["alpha"]
    assert {r["project_id"]: r["seconds"] for r in snapshot["by_project"]}.get(alpha) == 1800
    october = _collate(report_db)
    assert all(b["start_local"] < "2026-11-01" for b in october["blocks"])


def test_multi_project_filter_applies_to_blocks_pie_and_days(report_db):
    ids = report_db["ids"]
    only_alpha = _collate(report_db, [ids["alpha"]])
    assert {r["project_id"] for r in only_alpha["by_project"]} == {ids["alpha"]}
    assert {b["project_id"] for b in only_alpha["blocks"]} == {ids["alpha"]}
    assert only_alpha["project_key"] == str(ids["alpha"])
    both = _collate(report_db, [ids["beta"], month.UNASSIGNED_ID])
    assert {r["project_id"] for r in both["by_project"]} == {ids["beta"], month.UNASSIGNED_ID}
    assert all(set(d["projects"]) <= {str(ids["beta"]), "0"} for d in both["by_day"])


def test_saved_report_round_trip_is_scoped_to_period_and_projects(report_db):
    snapshot = _collate(report_db, [report_db["ids"]["alpha"]])
    analysis = {"summary": "Alpha checkout work.", "projects": [], "notes": []}
    saved = month.save_report(snapshot, analysis, provider="openrouter", model="deepseek/deepseek-v4.1-flash",
                              get_session=report_db["session"])
    period = month.month_period("2026-10", today=date(2026, 11, 6))
    found = month.latest_report(period, [report_db["ids"]["alpha"]], report_db["session"])
    assert found["id"] == saved["id"] and found["analysis"]["summary"] == "Alpha checkout work."
    assert month.latest_report(period, None, report_db["session"]) is None
    assert month.latest_report(period, [report_db["ids"]["alpha"]], report_db["session"], include_automations=True) is None


def test_decode_analysis_accepts_fenced_json_and_rejects_prose():
    data = month._decode_analysis('```json\n{"summary": "S", "projects": [{"name": "A", "hours": 2, "highlights": ["x"]}]}\n```')
    assert data["summary"] == "S" and data["projects"][0]["highlights"] == ["x"]
    with pytest.raises(ValueError):
        month._decode_analysis("no json here")


def test_pdf_renders(report_db):
    pytest.importorskip("reportlab")
    snapshot = _collate(report_db)
    snapshot["saved"] = {"analysis": {"summary": "Summary", "projects": [{"name": "Alpha", "hours": 1.5, "highlights": ["Checkout"]}],
                                      "notes": ["Codex not found"]}, "provider": "openrouter", "model": "m", "created_at": "2026-11-06T10:00:00Z"}
    pdf = month.render_pdf(snapshot)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


def test_existing_lists_still_behave(report_db):
    time_log = list_time_entries(limit=10)
    assert len(time_log["items"]) == 3
    assert {row["source"] for row in time_log["items"]} == {"play", "auto_pause"}
    runs = list_reports(limit=10)
    kinds = {row["kind"] for row in runs["items"]}
    assert kinds == {"execution"}
    assert len(runs["items"]) == 3


def test_cross_project_overlap_stays_in_the_project_sum(report_db):
    """The chart adds per-project clock times. Global clock counts a shared minute once."""
    ids = report_db["ids"]
    with report_db["session"]() as db:
        chat = Chat(title="Beta overlap", project_id=ids["beta"], params="{}")
        db.add(chat)
        db.flush()
        item = DevelopmentWorkItem(chat_id=chat.id, identity_key=f"thread:{chat.id}", project_id=ids["beta"],
                                   ticket_title="Same hour as Alpha")
        db.add(item)
        db.flush()
        db.add(DevelopmentTimeEntry(work_item_id=item.id, chat_id=chat.id, started_at=datetime(2026, 10, 10, 8, 0),
                                    ended_at=datetime(2026, 10, 10, 9, 0), seconds=3600, source="play"))
        db.commit()
    before = _collate(report_db)
    # Re-collate is the overlapped snapshot; the shared hour is already inside Alpha's clock.
    project_sum = sum(row["seconds"] for row in before["by_project"])
    assert project_sum - before["totals"]["seconds"] == 3600
    beta = next(row for row in before["by_project"] if row["project_id"] == ids["beta"])
    alpha = next(row for row in before["by_project"] if row["project_id"] == ids["alpha"])
    assert alpha["seconds"] == 3600
    assert beta["seconds"] == 1200 + 40 * 60 + 3600


def test_first_start_ignores_work_that_ended_before_the_period(report_db):
    """September work that does not overlap October is not the period's earliest block."""
    ids = report_db["ids"]
    with report_db["session"]() as db:
        chat = db.query(Chat).filter(Chat.title == "Alpha thread").one()
        item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.chat_id == chat.id).one()
        db.add(DevelopmentTimeEntry(work_item_id=item.id, chat_id=chat.id, started_at=datetime(2026, 9, 1, 8, 0),
                                    ended_at=datetime(2026, 9, 1, 9, 0), seconds=3600, source="play"))
        db.commit()
    october = _collate(report_db)
    alpha = next(row for row in october["by_project"] if row["project_id"] == ids["alpha"])
    assert alpha["first_start_local"] == "2026-10-10T10:00:00"
    assert all(not block["job_start"].startswith("2026-09-01") for block in october["blocks"])


def test_clock_time_and_recorded_time_stay_distinct(report_db):
    snapshot = _collate(report_db)
    ids = report_db["ids"]
    by_project = {row["project_id"]: row for row in snapshot["by_project"]}
    alpha = by_project[ids["alpha"]]
    assert alpha["seconds"] == 3600
    assert alpha["activity_seconds"] == 3600 + 1800
    assert alpha["first_start_local"] == "2026-10-10T10:00:00"
    assert snapshot["totals"]["seconds"] == 9000
    assert snapshot["totals"]["activity_seconds"] == 10800
    sources = {row["source"]: row for row in snapshot["sources"]}
    assert sources["cursor_local"]["state"] == "not_read"
    assert sources["claude_local"]["state"] == "unsupported"


def test_clipped_calendar_bar_keeps_the_recorded_start(report_db):
    with report_db["session"]() as db:
        item = db.query(DevelopmentWorkItem).filter(DevelopmentWorkItem.ticket_title == "Build checkout").one()
        db.add(DevelopmentTimeEntry(
            work_item_id=item.id, chat_id=item.chat_id, started_at=datetime(2026, 9, 30, 20, 0),
            ended_at=datetime(2026, 10, 1, 1, 0), seconds=5 * 3600, source="play"))
        db.commit()
    snapshot = _collate(report_db)
    block = next(row for row in snapshot["blocks"] if row["job_start"].startswith("2026-09-30"))
    assert block["starts_before_period"] is True
    assert block["job_start_local"] == "2026-09-30T22:00:00"
    assert block["start_local"] == "2026-10-01T00:00:00"
    alpha = next(row for row in snapshot["by_project"] if row["project_id"] == report_db["ids"]["alpha"])
    assert alpha["first_start_local"] == "2026-09-30T22:00:00"


def test_period_costs_sum_each_ledger_row_once():
    rows = [
        {"project_id": 1, "day_sast": "2026-10-10", "blended_cost_usd": 1.5, "provider_cost_usd": 1.0, "resource_cost_usd": 0.5},
        {"project_id": 1, "day_sast": "2026-10-11", "blended_cost_usd": 0.25, "provider_cost_usd": 0.25, "resource_cost_usd": 0},
        {"project_id": None, "day_sast": "2026-10-12", "provider_cost_usd": 0.4, "resource_cost_usd": 0.1},
        {"project_id": 2, "day_sast": "2026-11-01", "blended_cost_usd": 9},
        {"project_id": 2, "day_sast": "2026-09-30", "blended_cost_usd": 9},
    ]
    summary = month.summarize_period_costs(
        rows, start=date(2026, 10, 1), end=date(2026, 10, 31), project_names={1: "Alpha"})
    assert summary["entries"] == 3
    assert summary["total_cost_usd"] == 2.25
    by_project = {row["project_id"]: row for row in summary["by_project"]}
    assert by_project[1]["cost_usd"] == 1.75 and by_project[1]["entries"] == 2
    assert by_project[0]["name"] == "Unassigned" and by_project[0]["cost_usd"] == 0.5
    only_alpha = month.summarize_period_costs(
        rows, start=date(2026, 10, 1), end=date(2026, 10, 31), project_ids=[1], project_names={1: "Alpha"})
    assert only_alpha["entries"] == 2 and only_alpha["total_cost_usd"] == 1.75


def test_ledger_day_cuts_at_sast_midnight():
    late = datetime(2026, 10, 31, 22, 30, tzinfo=UTC).timestamp()
    early = datetime(2026, 10, 31, 21, 30, tzinfo=UTC).timestamp()
    assert month.ledger_day_sast(late) == "2026-11-01"
    assert month.ledger_day_sast(early) == "2026-10-31"
    assert month.ledger_day_sast(None) == ""


def test_deepseek_41_label_is_not_substituted(monkeypatch):
    monkeypatch.setattr(month, "default_model", lambda: {"provider": "ollama", "model": "qwen3:8b"})
    verdict = month.report_route_verdict("openrouter", "DeepSeek 4.1")
    assert verdict["blocked_unverified_label"] is True
    assert verdict["substituted"] is False
    assert verdict["selected"] == {"provider": "openrouter", "model": "DeepSeek 4.1"}
    assert verdict["requested"]["verified"] is False
    assert "does not rename" in verdict["limitation"]
    called = {}
    monkeypatch.setattr(month, "collate", lambda *args, **kwargs: called.setdefault("collate", True))
    monkeypatch.setattr(month, "run_analysis", lambda *args, **kwargs: called.setdefault("run", True))
    with pytest.raises(ValueError, match="not a model id"):
        month.build_period_report(
            month.month_period("2026-10", today=date(2026, 11, 6)), None,
            provider="openrouter", model="DeepSeek 4.1")
    assert called == {}
    explicit = month.report_route_verdict("openrouter", "deepseek/deepseek-v4.1-flash")
    assert explicit["blocked_unverified_label"] is False
    assert explicit["selected"]["model"] == "deepseek/deepseek-v4.1-flash"
    configured = month.report_route_verdict(None, None)
    assert configured["selected"] == {"provider": "ollama", "model": "qwen3:8b"}


def test_build_stores_an_explicit_model_id_unchanged(monkeypatch, report_db):
    snapshot = _collate(report_db)
    snapshot["blocks"] = []
    monkeypatch.setattr(month, "collate", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(month, "default_model", lambda: {"provider": "ollama", "model": "qwen3:8b"})
    monkeypatch.setattr(month, "safe_period_costs", lambda *args, **kwargs: {"available": False, "reason": "fixture"})

    def save(snap, analysis, *, provider, model, get_session=None):
        return {"id": 1, "provider": provider, "model": model, "analysis": analysis}

    monkeypatch.setattr(month, "save_report", save)
    monkeypatch.setattr(month, "run_analysis", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model called")))
    result = month.build_period_report(
        month.month_period("2026-10", today=date(2026, 11, 6)), None,
        provider="openrouter", model="deepseek/deepseek-v4.1-flash")
    assert result["saved"]["provider"] == "openrouter"
    assert result["saved"]["model"] == "deepseek/deepseek-v4.1-flash"
    assert result["analysis_route"]["substituted"] is False
    assert result["costs"]["available"] is False


def test_history_keeps_real_fields_and_drops_invented_execution_names(report_db):
    from distr.core.reports.service import list_work_history

    payload = list_work_history(limit=20)
    assert len(payload["items"]) == 6
    assert all("source" not in row and "kind" not in row for row in payload["items"])
    descriptions = [row["description"] for row in payload["items"]]
    assert "Build checkout" in descriptions
    assert "Loose thread" in descriptions
    untitled = [row for row in payload["items"] if row["description"] is None]
    assert len(untitled) == 3
    assert {row["status"] for row in untitled} == {"completed"}
    checkouts = [row for row in payload["items"] if row["description"] == "Build checkout"]
    assert {row["duration_seconds"] for row in checkouts} == {3600, 1800}
    assert all(row["project_name"] == "Alpha" and row["status"] is None and row["started_at"] and row["ended_at"] for row in checkouts)
    loose = next(row for row in payload["items"] if row["description"] == "Loose thread")
    assert loose["project_name"] is None
    assert "unknown" in payload["counting"]["unknown"].lower() or "Missing" in payload["counting"]["unknown"]


# ---------------------------------------------------------------- allocation and wording

def test_project_matcher_resolves_alias_paths_to_one_project():
    matcher = month.ProjectMatcher({
        9: {"id": 9, "name": "DecisionsAI", "folder": "/work/DECISIONS/DecisionsAI", "triggers": ["decisions"]},
        10: {"id": 10, "name": "Spoortrack", "folder": "/work/SPOORTRACK/www.spoortrack.com", "triggers": ["new spoor track"]},
        7: {"id": 7, "name": "Spoortrack (Old)", "folder": "/work/SPOORTRACK/OLD/www.spoortrack.com/webapp", "triggers": ["old spoor track"]},
    })
    assert matcher.match("/work/DECISIONS/DecisionsAI/examples/pizza-house") == 9  # folder prefix still wins
    assert matcher.match("/work/DECISIONS") == 9            # parent path resolves by trigger
    assert matcher.match("/work/SPOORTRACK") == 10          # parent path resolves by exact name, not (Old)
    assert matcher.match("/work/SPOORTRACK/OLD/www.spoortrack.com") == 7  # ancestor of exactly one folder
    assert matcher.match("/Users/paul/Downloads") is None    # unrelated path is never guessed


def test_project_matcher_refuses_an_ambiguous_alias():
    matcher = month.ProjectMatcher({
        3: {"id": 3, "name": "Tensology", "folder": "/a", "triggers": ["acme"]},
        4: {"id": 4, "name": "Other", "folder": "/b", "triggers": ["acme"]},
    })
    assert matcher.match("/x/acme") is None


def test_orphaned_thread_timer_is_dropped_not_shown_as_a_thread(report_db):
    with report_db["session"]() as db:
        item = DevelopmentWorkItem(chat_id=9001, identity_key="thread:9001")  # no Chat row 9001
        db.add(item)
        db.flush()
        db.add(DevelopmentTimeEntry(work_item_id=item.id, chat_id=9001,
                                    started_at=datetime(2026, 10, 17, 8, 0), ended_at=datetime(2026, 10, 17, 9, 0),
                                    seconds=3600, source="play"))
        db.commit()
    snapshot = _collate(report_db)
    assert all(block["ref"] != "chat:9001" for block in snapshot["blocks"])
    assert any("deleted threads" in note for note in snapshot["notes"])


def test_reused_newer_work_item_cannot_assign_an_older_deleted_chat_timer(report_db):
    with report_db['session']() as db:
        pid = db.query(Project).first().id
        item = DevelopmentWorkItem(chat_id=55, identity_key='new-item', project_id=pid,
                                   ticket_title='Unrelated newer task', created_at=datetime(2026, 10, 31))
        db.add(item)
        db.flush()
        db.add(DevelopmentTimeEntry(work_item_id=item.id, chat_id=9002,
            started_at=datetime(2026, 10, 17, 8), ended_at=datetime(2026, 10, 17, 9), seconds=3600, source='play'))
        db.commit()
    row = next(b for b in _collate(report_db)['blocks'] if b['ref'] == 'chat:9002')
    assert row['project_id'] == 0 and row['title'] != 'Unrelated newer task'
    assert row['project_provenance'] == 'historical_metadata_conflict'
    assert row['seconds'] == 3600


def test_client_wording_softens_repair_language():
    assert month._soften_wording("Fixed the login bug") == "Updated the login issue"
    assert month._soften_wording("Investigated a crash") == "Investigated a stability issue"
    assert month._soften_wording("Add checkout flow") == "Add checkout flow"


def test_nearby_commits_cannot_overwrite_recorded_work_descriptions(report_db, monkeypatch):
    monkeypatch.setattr(month, '_git_commit_subjects', lambda *a: [(datetime(2026, 10, 17, 8), 'Unrelated repeated commit title')])
    snapshot = _collate(report_db)
    assert all(block['title'] != 'Unrelated repeated commit title' for block in snapshot['blocks'])


def test_explicit_project_correction_is_applied_before_project_filter(report_db, monkeypatch):
    before = _collate(report_db)
    row = next(b for b in before['blocks'] if b['source'] == 'decisions_thread' and b['project_id'] == report_db['ids']['alpha'])
    beta = report_db['ids']['beta']
    monkeypatch.setattr('distr.core.reports.projection_store.manual_project_map', lambda *a: {(row['source'], row['ref']): beta})
    selected = _collate(report_db, [beta])
    assert any(b['ref'] == row['ref'] and b['project_id'] == beta for b in selected['blocks'])


def test_throwaway_working_directories_are_not_counted_as_work():
    assert month._is_test_cwd("/private/tmp/decisionsai-local-worker-proof-2/project")
    assert month._is_test_cwd("/work/sandbox/scratch")
    assert month._is_test_cwd("/Users/paul/.decisions/work/email-action-items")
    assert not month._is_test_cwd("/Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI")


def test_short_local_activity_keeps_exact_elapsed_time_and_never_pads():
    start = datetime(2026, 10, 23, 8, 0, 0, 123456)
    end = start + timedelta(seconds=14, microseconds=654321)
    assert month.activity_intervals([start, end]) == [(start, end)]
    assert month.activity_intervals([start]) == []
    assert month.activity_intervals([start, start]) == []


def test_duplicate_play_timers_and_multi_turn_pi_session_collapse_to_two_runs():
    from datetime import datetime, timedelta
    from distr.core.reports.month import combine_duplicate_runs

    base = datetime(2026, 11, 11, 8)
    at = lambda seconds: base + timedelta(seconds=seconds)
    reply = 'A detailed completed reply about the first confirmed change. ' * 2
    first = dict(source='pi_decisions', family='pi', ref='execution:1', project_id=2,
                 start=at(1), end=at(300), execution_chat_id=19,
                 instruction_hash='first', reported_output='Progress. ' + reply.strip())
    second = {**first, 'ref': 'execution:2', 'start': at(490), 'end': at(760),
              'instruction_hash': 'second'}
    timers = [{**r, 'ref': 'chat:19', 'source': 'decisions_thread',
               'timer_chat_id': 19, 'start': r['start']-timedelta(seconds=2)}
              for r in (first, second)]
    local = dict(source='pi_local', family='pi', ref='pi_local:fixture', project_id=2,
                 start=at(2), end=at(759), instruction_hash='wrapped-first',
                 local_turns=[(at(2), at(299), 'wrapped-first', reply.strip()),
                              (at(491), at(759), 'second', '')])
    independent = {**timers[0], 'ref': 'chat:21', 'timer_chat_id': 21}
    result = combine_duplicate_runs([*timers, first, second, local, independent])
    assert [r['ref'] for r in result] == ['execution:1', 'execution:2', 'chat:21']
    assert all(r['related_refs'] == ['pi_local:fixture', 'chat:19'] for r in result[:2])
    assert [(r['start'], r['end']) for r in result[:2]] == [
        (first['start'], first['end']), (second['start'], second['end'])]

    unmatched = {**local, 'local_turns': [*local['local_turns'][:1], (at(491), at(759), 'other', '')]}
    assert any(r['ref'] == local['ref'] for r in combine_duplicate_runs([first, second, unmatched]))


def test_auto_paused_thread_timer_links_to_execution_without_hiding_manual_entry(report_db):
    with report_db['session']() as db:
        item = db.query(DevelopmentWorkItem).filter_by(ticket_title='Build checkout').one()
        start = datetime(2026, 10, 13, 8)
        for source in ('auto_pause', 'manual'):
            db.add(DevelopmentTimeEntry(work_item_id=item.id, chat_id=item.chat_id,
                                        started_at=start, ended_at=start+timedelta(seconds=112),
                                        seconds=112, source=source))
        db.add(ProjectExecutionSession(project_id=item.project_id, route_backend='pi', route_type='project_cli',
                                       origin='development', status='completed',
                                       input_packet=json.dumps({'chat_id': item.chat_id}),
                                       started_at=start+timedelta(seconds=1), completed_at=start+timedelta(seconds=110)))
        db.commit()
    result = month.collate(month.resolve_period(start='2026-10-13',end='2026-10-13'),
                           get_session=report_db['session'], include_local=False)
    assert sorted(b['source'] for b in result['blocks']) == ['decisions_thread', 'pi_decisions']
    run = next(b for b in result['blocks'] if b['source'] == 'pi_decisions')
    assert run['related_refs'] == [f'chat:{item.chat_id}'] and run['seconds'] == 109
