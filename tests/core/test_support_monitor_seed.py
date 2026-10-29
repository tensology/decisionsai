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
