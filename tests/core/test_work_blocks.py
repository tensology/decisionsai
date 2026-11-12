"""Time-entry blocks are scoped collections of work, not project-day roll-ups."""
from __future__ import annotations

from distr.core.reports.work_blocks import event_integrity, proposed_split, scoped_blocks


def iv(start, end, ref="", description=""):
    return {"start": start, "end": end, "ref": ref, "description": description}


def test_two_sessions_on_one_project_day_stay_two_blocks():
    blocks = scoped_blocks([
        iv("2026-10-28T10:26:00Z", "2026-10-28T10:50:00Z", "pi_local:a", "Spam protection"),
        iv("2026-10-28T10:55:00Z", "2026-10-28T11:10:00Z", "execution:5", "API reliability"),
    ], project_id=8)
    assert [(b["scope"], b["active_seconds"]) for b in blocks] == [("pi_local:a", 1440), ("execution:5", 900)]


def test_one_scope_splits_on_an_idle_gap_and_merges_inside_it():
    blocks = scoped_blocks([
        iv("2026-10-26T06:39:00Z", "2026-10-26T06:50:00Z", "pi_local:a"),
        iv("2026-10-26T07:05:00Z", "2026-10-26T07:20:00Z", "pi_local:a"),   # 15 min gap: same block
        iv("2026-10-26T12:00:00Z", "2026-10-26T12:30:00Z", "pi_local:a"),   # hours later: new block
    ], project_id=12)
    assert len(blocks) == 2
    assert blocks[0]["active_seconds"] == 26 * 60
    assert blocks[0]["span_seconds"] == 41 * 60


def test_blocks_split_at_sast_midnight():
    blocks = scoped_blocks([iv("2026-10-24T21:27:00Z", "2026-10-24T22:30:00Z", "pi_local:a")], project_id=4)
    assert [b["day"] for b in blocks] == ["2026-10-24", "2026-10-25"]


def test_blocks_never_cross_projects():
    blocks = scoped_blocks([
        {**iv("2026-11-02T08:00:00Z", "2026-11-02T08:10:00Z", "x"), "project_id": 2},
        {**iv("2026-11-02T08:05:00Z", "2026-11-02T08:20:00Z", "x"), "project_id": 14},
    ])
    assert sorted(b["project_id"] for b in blocks) == [2, 14]


def test_day_rollup_entry_is_reported_as_folded_and_reconstructed():
    event = {"id": 481, "project_id": 169, "billable": True,
             "start_at": "2026-10-28T17:55:00+00:00", "end_at": "2026-10-28T19:35:00+00:00"}
    proof = {"source_intervals": [
        iv("2026-10-28T10:26:00Z", "2026-10-28T10:50:00Z", "pi_local:a", "Spam protection"),
        iv("2026-10-28T19:20:00Z", "2026-10-28T19:35:00Z", "execution:5", "API reliability"),
    ]}
    integrity = event_integrity(event, proof)
    assert integrity["state"] == "folded" and integrity["scoped_blocks"] == 2 and integrity["reconstructed"]
    split = proposed_split(event, proof)
    assert [s["start_at"] for s in split] == ["2026-10-28T10:26:00Z", "2026-10-28T19:20:00Z"]
    assert all(s["billable"] and s["replaces_event_id"] == 481 for s in split)


def test_single_recorded_entry_is_coherent():
    event = {"id": 579, "project_id": 183, "billable": False,
             "start_at": "2026-11-06T08:00:00+00:00", "end_at": "2026-11-06T09:00:00+00:00"}
    proof = {"source_intervals": [iv("2026-11-06T08:00:00+00:00", "2026-11-06T09:00:00+00:00", "calendar_event:579")]}
    assert event_integrity(event, proof)["state"] == "coherent"
    assert event_integrity(event, None)["state"] == "no_source_evidence"


def test_reports_calendar_rows_carry_block_integrity(tmp_path):
    import json
    from distr.core.reports.calendar_review import apply_review
    data = {'schema': 1, 'period': {'start': '2026-10-28', 'end': '2026-10-28'},
            'reviewed_through': '2026-10-29T00:00:00Z', 'scope': 'synchronized_calendar', 'project_map': {'8': 169},
            'calendar': [{'id': 481, 'project_id': 169, 'title': 'Snuza day', 'billable': True, 'billing_excluded': False,
                          'start_at': '2026-10-28T17:55:00Z', 'end_at': '2026-10-28T19:35:00Z'}],
            'unbilled_event_ids': [481], 'holds': [],
            'evidence': {'481': {'source_intervals': [
                iv('2026-10-28T10:26:00Z', '2026-10-28T10:50:00Z', 'pi_local:a', 'Spam protection'),
                iv('2026-10-28T19:20:00Z', '2026-10-28T19:35:00Z', 'execution:5', 'API reliability')]}}}
    path = tmp_path / 'calendar_review.json'
    path.write_text(json.dumps(data))
    snapshot = {'period': data['period'], 'project_ids': [], 'totals': {}, 'by_family': [],
                'projects': [{'id': 8, 'name': 'Snuza'}], 'blocks': []}
    row = apply_review(snapshot, path=path)['blocks'][0]
    assert row['block_integrity']['state'] == 'folded'
    assert row['block_integrity']['scoped_blocks'] == 2
