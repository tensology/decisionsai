"""Unit tests for R27 initiative voice phrase helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "voice_commands",
    _ROOT / "distr/core/initiative/voice_commands.py",
)
_vc = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_vc)


def test_wants_agenda_readout():
    assert _vc.wants_agenda_readout("what's on my agenda")
    assert _vc.wants_agenda_readout("hey whats on my agenda today")
    assert not _vc.wants_agenda_readout("this is a long unrelated sentence " * 5)


def test_match_draft_decision():
    assert _vc.match_draft_decision("approve that") == "approve"
    assert _vc.match_draft_decision("reject") == "reject"
    assert _vc.match_draft_decision("go ahead") == "approve"
    assert _vc.match_draft_decision("please write a long essay about approval") is None


def test_match_reminder_request():
    r = _vc.match_reminder_request("remind me to call mom daily")
    assert r and r["instruction"] == "call mom" and r["frequency"] == "daily"
    r2 = _vc.match_reminder_request("remind me to sync repos every week.")
    assert r2 and r2["frequency"] == "weekly"


def test_wants_pending_draft_readout():
    assert _vc.wants_pending_draft_readout("any pending actions")
    assert _vc.wants_pending_draft_readout("read pending draft")
    assert not _vc.wants_pending_draft_readout("this is unrelated " * 10)


def test_match_voice_wait_cancel():
    assert _vc.match_voice_wait_cancel("never mind")
    assert _vc.match_voice_wait_cancel("cancel")
    assert not _vc.match_voice_wait_cancel("cancel the whole subscription plan today")


def test_match_schedule_confirm():
    assert _vc.match_schedule_confirm("confirm schedule")
    assert _vc.match_schedule_confirm("yes add it")
    assert not _vc.match_schedule_confirm("maybe confirm schedule later if possible")


def test_extract_draft_id_token():
    u = "550e8400-e29b-41d4-a716-446655440000"
    assert _vc.extract_draft_id_token(f"read draft {u}") == u
    assert _vc.extract_draft_id_token(f"approve {u}") == u
    assert _vc.extract_draft_id_token("approve 550e8400") == "550e8400"


def test_match_draft_decision_for_id():
    u = "550e8400-e29b-41d4-a716-446655440000"
    r = _vc.match_draft_decision_for_id(f"approve draft {u}")
    assert r == ("approve", u)
    r2 = _vc.match_draft_decision_for_id(f"reject id {u}")
    assert r2 == ("reject", u)
    assert _vc.match_draft_decision_for_id("approve draft") is None


def test_match_read_draft_by_id_request():
    u = "550e8400-e29b-41d4-a716-446655440000"
    assert _vc.match_read_draft_by_id_request(f"read draft {u}") == u
    assert _vc.match_read_draft_by_id_request("read pending draft") is None


def test_resolve_draft_entry_by_voice_id():
    from distr.core.initiative.draft_queue import DraftEntry

    iso = "2030-01-01T00:00:00+00:00"
    a = "550e8400-e29b-41d4-a716-446655440000"
    b = "660e8400-e29b-41d4-a716-446655440001"
    e1 = DraftEntry(
        id=a,
        action_type="x",
        description="one",
        draft="d1",
        reason="r",
        created_at=iso,
        expires_at=iso,
    )
    e2 = DraftEntry(
        id=b,
        action_type="x",
        description="two",
        draft="d2",
        reason="r",
        created_at=iso,
        expires_at=iso,
    )
    ent, st = _vc.resolve_draft_entry_by_voice_id(a, [e1, e2])
    assert st == "one" and ent is e1
    ent2, st2 = _vc.resolve_draft_entry_by_voice_id("550e8400", [e1, e2])
    assert st2 == "one" and ent2 is e1
    # Same 8-char prefix on two different UUIDs → ambiguous
    e1b = DraftEntry(
        id="550e8400-aaaa-41d4-a716-446655440099",
        action_type="x",
        description="c",
        draft="d",
        reason="r",
        created_at=iso,
        expires_at=iso,
    )
    ent4, st4 = _vc.resolve_draft_entry_by_voice_id("550e8400", [e1, e1b])
    assert st4 == "ambiguous"
