"""Incoming proposals: Tensology is canonical, local candidates are deduped against it."""

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


def _incoming_item(source, thread_id, text, label="Client", project_id=None, project_name=""):
    return {
        "key": f"{source}:{thread_id}",
        "source": source,
        "source_thread_id": thread_id,
        "conversation_label": label,
        "sender": label,
        "text": text,
        "project_id": project_id,
        "project_name": project_name,
        "created_at": "2026-10-29T08:00:00+00:00",
    }


def test_tensology_proposals_kept_and_local_deduped(monkeypatch):
    shared = "120363339060471282@g.us"
    # Tensology: two proposals sharing one thread (both must survive) + one other.
    monkeypatch.setattr(
        "distr.core.tensology_client.fetch_pending_work_requests",
        lambda: [
            _tensology_row(45, shared, "whatsapp", "Check the first shipping cost"),
            _tensology_row(46, shared, "whatsapp", "Investigate incorrect shipping cost"),
            _tensology_row(47, "email-abc", "mailshot", "Reconnect GitLab"),
        ],
    )
    # Local: one item on the shared thread (suppress) + one new thread (keep).
    monkeypatch.setattr(
        "distr.core.incoming.service.list_development_incoming",
        lambda limit=100, **kwargs: {
            "items": [
                _incoming_item("whatsapp", shared, "Please fix the shipping cost calculation"),
                _incoming_item(
                    "gmail", "thread-new", "Can you please send the invoice?",
                    label="Acme", project_id=7, project_name="Acme",
                ),
            ]
        },
    )

    result = proposals_mod.list_incoming_proposals(limit=50)
    tensology = [p for p in result["proposals"] if p["origin"] == "tensology"]
    local = [p for p in result["proposals"] if p["origin"] == "local"]

    assert len(tensology) == 3  # never deduped among themselves
    assert {p["source_thread_id"] for p in local} == {"thread-new"}  # shared thread suppressed
    assert result["counts"].get("gmail") == 1


def test_local_candidate_skips_noise_and_non_actionable(monkeypatch):
    monkeypatch.setattr("distr.core.tensology_client.fetch_pending_work_requests", lambda: [])
    monkeypatch.setattr(
        "distr.core.incoming.service.list_development_incoming",
        lambda limit=100, **kwargs: {
            "items": [
                _incoming_item("gmail", "t1", "Weekly newsletter digest", label="newsletter@x.com"),
                _incoming_item("gmail", "t2", "Thanks, see you then", label="Friend"),
            ]
        },
    )
    result = proposals_mod.list_incoming_proposals(limit=50)
    assert result["proposals"] == []
