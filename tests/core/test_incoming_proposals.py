"""Incoming proposals: Tensology is canonical, local triage is deduped against it."""

from __future__ import annotations

from distr.core.incoming import proposals as proposals_mod


def _tensology_row(pid, thread_id, source, title):
    return {
        "id": pid,
        "status": "pending",
        "source": source,
        "title": title,
        "project": {"id": 1, "name": "Merrypak"},
        "source_conversation": {"thread_id": thread_id},
        "evidence": [{"sent_at": "2026-10-29T07:00:00+00:00"}],
    }


def _incoming_item(source, thread_id, text, label="Client"):
    return {
        "key": f"{source}:{thread_id}",
        "source": source,
        "source_thread_id": thread_id,
        "conversation_label": label,
        "sender": label,
        "text": text,
        "created_at": "2026-10-29T08:00:00+00:00",
    }


def test_tensology_proposals_kept_and_local_deduped(monkeypatch):
    shared = "120363339060471282@g.us"
    monkeypatch.setattr(
        "distr.core.tensology_client.fetch_pending_work_requests",
        lambda: [
            _tensology_row(45, shared, "whatsapp", "Check the first shipping cost"),
            _tensology_row(46, shared, "whatsapp", "Investigate incorrect shipping cost"),
            _tensology_row(47, "email-abc", "mailshot", "Reconnect GitLab"),
        ],
    )
    monkeypatch.setattr(
        "distr.core.incoming.service.list_development_incoming",
        lambda limit=100, **kwargs: {"items": []},
    )
    # A local proposal on the same thread must be suppressed; a new thread must survive.
    monkeypatch.setattr(
        "distr.core.incoming.proposals._local_proposals",
        lambda items, settings: [
            {"id": f"local:whatsapp:{shared}", "origin": "local", "dedupe_key": f"whatsapp:{shared}",
             "source": "whatsapp", "source_thread_id": shared, "title": "duplicate"},
            {"id": "local:gmail:thread-new", "origin": "local", "dedupe_key": "gmail:thread-new",
             "source": "gmail", "source_thread_id": "thread-new", "title": "new work"},
        ],
    )

    result = proposals_mod.list_incoming_proposals(limit=50)
    tensology = [p for p in result["proposals"] if p["origin"] == "tensology"]
    local = [p for p in result["proposals"] if p["origin"] == "local"]

    assert len(tensology) == 3  # never deduped among themselves
    assert [p["source_thread_id"] for p in local] == ["thread-new"]  # shared thread suppressed


def test_triage_builds_proposal_from_llm(monkeypatch):
    monkeypatch.setattr(
        "distr.core.incoming.proposals._candidate_projects",
        lambda limit=50: [{"id": 7, "name": "Merrypak", "description": "", "open_tickets": []}],
    )
    items = [_incoming_item("whatsapp", "t1", "Please fix the shipping cost", label="Carmen")]

    def fake_complete(provider, model, messages, settings):
        return (
            '```json\n{"is_actionable": true, "project_id": 7, "title": "Fix shipping cost", '
            '"description": "The first shipping cost is wrong.", "tasks": ["a", "b"], '
            '"priority": "high", "estimated_minutes": 30, "confidence": 0.9, "rationale": "client asks"}\n```'
        )

    out = proposals_mod._triage_conversations(items, {}, complete=fake_complete)
    assert len(out) == 1
    proposal = out[0]
    assert proposal["origin"] == "local" and proposal["project_id"] == 7
    assert proposal["title"] == "Fix shipping cost"
    assert proposal["tasks"] == ["a", "b"] and proposal["priority"] == "high"
    assert proposal["estimated_minutes"] == 30


def test_triage_skips_non_actionable_and_unknown_project(monkeypatch):
    monkeypatch.setattr(
        "distr.core.incoming.proposals._candidate_projects",
        lambda limit=50: [{"id": 7, "name": "Merrypak", "description": "", "open_tickets": []}],
    )
    items = [_incoming_item("whatsapp", "t1", "Please fix it", label="Client")]

    assert proposals_mod._triage_conversations(items, {}, complete=lambda *a, **k: '{"is_actionable": false}') == []
    assert proposals_mod._triage_conversations(
        items, {}, complete=lambda *a, **k: '{"is_actionable": true, "project_id": 999, "title": "x"}'
    ) == []


def test_triage_skips_own_messages_before_calling_the_model(monkeypatch):
    called = []
    monkeypatch.setattr(
        "distr.core.incoming.proposals._candidate_projects",
        lambda limit=50: [{"id": 7, "name": "X", "description": "", "open_tickets": []}],
    )
    items = [dict(_incoming_item("whatsapp", "t1", "ok", label="Paul Hoft"), from_me=True)]
    out = proposals_mod._triage_conversations(items, {}, complete=lambda *a, **k: called.append(1) or "{}")
    assert out == [] and called == []
