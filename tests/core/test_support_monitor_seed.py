"""Q5 fix 2026-10-29: stale seeding from the Grok Bot state file, and a clean headline."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from distr.core.automation.support_monitor import SupportMonitor, plain_headline, seeded_stale_keys

GROK_STALE = [
    {"item": "MvLogic Krish Govender, Commerce Launch Proposal (Quote TS-CL-001)", "owner": "Sales"},
    {"item": "AuctionNow board: 2 In Progress (158,159) and 3 Backlog idle since 2026-09-03", "owner": "Platform"},
    {"item": "auto_workflow_run 166 (workflow 430) waiting since 2026-10-24 21:09 on ticket 287", "owner": "Platform"},
]


def test_seeded_stale_keys_maps_ids_and_ignores_email_proposals():
    keys = seeded_stale_keys(GROK_STALE + [{"item": "My Board #227 idle"}])
    assert keys == ["run:166", "ticket:158", "ticket:159", "ticket:227"]
    assert seeded_stale_keys(None) == [] and seeded_stale_keys([{"item": "no ids here"}]) == []


def test_plain_headline_strips_markdown_stars():
    assert plain_headline("**0 new items, 0 need action.** 17 newly stale.") == "0 new items, 0 need action. 17 newly stale."


def _min_db(path):
    import sqlite3

    con = sqlite3.connect(path)
    con.executescript(
        """
        create table kanban_boards (id integer primary key, name text, archived integer default 0);
        create table kanban_lanes (id integer primary key, name text, board_id integer, position integer default 0);
        create table kanban_tickets (id integer primary key, title text, modified_date text, lane_id integer, created_date text);
        create table auto_workflow_runs (id integer primary key, workflow_id integer, started_at text, status text, ticket_id integer);
        create table skill_judgment_proposals (id integer primary key, skill_name text, status text, updated_at real, created_at real);
        """
    )
    con.commit()
    con.close()


def test_tensology_proposals_from_site_api_are_watched(tmp_path):
    """The monitor reads client proposals from the Tensology site API, not a hardcoded list."""
    db = tmp_path / "m.db"
    _min_db(db)
    now = datetime(2026, 10, 29, 10, 30, tzinfo=timezone.utc)
    mon = SupportMonitor(
        db_path=db,
        state_file=tmp_path / "state-decisions.json",
        seed_state_file=None,
        now=now,
        port_check=lambda: False,
        tensology_fetch=lambda: [
            {"id": 47, "status": "pending", "title": "Investigate courier cost increase",
             "project": {"name": "Merrypak"}, "evidence": [{"sent_at": "2026-10-20T09:00:00+00:00"}]},
            {"id": 48, "status": "pending", "title": "Fresh proposal",
             "project": {"name": "Jackcat"}, "evidence": [{"sent_at": "2026-10-29T08:00:00+00:00"}]},
        ],
    )
    saved = {}
    res = mon.run(write_state=saved.update, force=True)
    assert res["channel_status"]["tensology"] == "ok (2 pending proposal(s))"
    assert "Tensology proposal 47" in res["markdown"] and "Tensology proposal 48" in res["markdown"]
    assert "proposal waiting >2 working days" in res["markdown"]
    assert res["newly_stale"] >= 2


def test_tensology_proposals_absent_when_not_configured(tmp_path):
    db = tmp_path / "m.db"
    _min_db(db)
    mon = SupportMonitor(
        db_path=db, state_file=tmp_path / "s.json", seed_state_file=None,
        now=datetime(2026, 10, 29, 10, 30, tzinfo=timezone.utc), port_check=lambda: False,
    )
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        assert mon._tensology_proposals(con) == []
    finally:
        con.close()
    assert mon.channel_status["tensology"].startswith("error")


def test_first_run_seeds_stale_reported_read_only(tmp_path):
    seed = tmp_path / "state.json"
    seed.write_text(json.dumps({"last_seen": {}, "stale_proposals": GROK_STALE}))
    before = seed.read_text()
    mon = SupportMonitor(db_path=tmp_path / "none.db", state_file=tmp_path / "state-decisions.json", seed_state_file=seed,
                         now=datetime(2026, 10, 29, 10, 30, tzinfo=timezone.utc))
    state = mon.load_state()
    assert state["stale_reported"] == ["run:166", "ticket:158", "ticket:159"]
    assert state["seeded_from"] == "state.json"
    assert seed.read_text() == before  # the Grok Bot file is never written
