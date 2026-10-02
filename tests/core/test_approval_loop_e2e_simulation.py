"""Simulate Telegram callback handlers for the WA approval loop (no live Paul press)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

from distr.core.kanban import whatsapp_work_lifecycle as lifecycle
from distr.core.workflow.dispatcher import DEFAULT_RUN_SETTINGS, _workflow_run_settings
from distr.core.workflow.run_briefing import human_checkpoint_enabled


def _isolated_engine(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    monkeypatch.setattr(lifecycle, "engine", engine)
    lifecycle.ensure_tables()
    return engine


def test_a_to_e_whatsapp_approval_loop_simulated(monkeypatch, tmp_path):
    """A→E: intake lifecycle → verify → deploy → Yes → WA dry-run send."""
    engine = _isolated_engine(monkeypatch, tmp_path)

    # A) WA group + project allocation → intake lifecycle
    row = lifecycle.record_ticket_created(
        ticket_id=9001,
        board_id=7,
        project_id=3,
        source_jid="120363@g.us",
        source_phone="120363",
        source_contact="Client group",
        message_ids=[101, 102],
    )
    assert row["status"] == "ticket_created"

    # B/C) human_checkpoints default ON (TG pre-work approve would fire on workflow start)
    assert DEFAULT_RUN_SETTINGS["human_checkpoints"] is True
    assert human_checkpoint_enabled({"run_settings": _workflow_run_settings(SimpleNamespace(run_settings="{}"))})
    assert human_checkpoint_enabled({"ticket_id": 9001})

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.get.return_value = SimpleNamespace(title="Fix checkout")
    monkeypatch.setattr(lifecycle, "get_session", lambda: session)
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_clear_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "notify_telegram_verification", lambda *_a, **_k: True)
    monkeypatch.setattr(lifecycle, "notify_telegram_deploy", lambda *_a, **_k: True)

    # D1) Completion starts verification (not client draft yet)
    gate = lifecycle.begin_post_completion_gates(
        ticket_id=9001,
        run_id=55,
        status="completed",
        result_summary="Checkout passes.",
    )
    assert gate and gate["gate_kind"] == "verification"
    assert lifecycle.prepare_completed_reply(
        ticket_id=9001, run_id=55, status="completed", result_summary="Checkout passes.",
    ) is None

    # D2) Verify OK → deploy assist
    deploy = lifecycle.handle_telegram_reply(f"wv:{gate['token']}:ok", chat_id=42)
    assert deploy["waiting_kind"] == lifecycle.WAITING_KIND_DEPLOY

    # D3) Settle deploy → client "send to customer?" review
    captured = {}

    class FakeManager:
        def send_to_telegram(self, message, reply_markup=None):
            captured["text"] = message
            captured["reply_markup"] = reply_markup
            return True

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: FakeManager(),
    )
    settled = lifecycle.handle_telegram_reply(f"wd:{deploy['token']}:prod", chat_id=42)
    assert settled["action"] == "deploy_settled"
    assert settled["deploy_target"] == "production"
    assert "send to customer" in settled["text"].lower()
    assert captured.get("text") and "send to customer" in captured["text"].lower()

    # E) Yes → WA send (DRY-RUN)
    monkeypatch.setenv("DECISIONSAI_WHATSAPP_DRY_RUN", "1")
    import distr.core.integrations.whatsapp.relay_client as relay

    posts = []
    monkeypatch.setattr(
        relay.requests,
        "post",
        lambda *a, **k: posts.append(1) or (_ for _ in ()).throw(AssertionError("no live POST")),
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.delete_compose_draft",
        lambda *_a, **_k: None,
    )
    sent = lifecycle.handle_telegram_reply(f"wa:{settled['token']}:send", chat_id=42)
    assert sent["handled"] is True
    assert "sent" in sent["text"].lower()
    assert posts == []
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=9001")
        ).scalar_one() == "reply_sent"
        assert conn.execute(
            text("SELECT deploy_target FROM whatsapp_work_lifecycles WHERE ticket_id=9001")
        ).scalar_one() == "production"


def test_f_small_adhoc_still_gets_human_checkpoints_default():
    """F) Small ad-hoc (no explicit workflow setting) still enables pre-work approve."""
    settings = _workflow_run_settings(SimpleNamespace(run_settings=json.dumps({})))
    assert settings["human_checkpoints"] is True
    assert human_checkpoint_enabled({"run_settings": settings, "ticket_id": 1})
    assert human_checkpoint_enabled({"ticket_id": 1})  # missing run_settings → default ON


def test_g_verification_reject_then_no_client_draft(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=9002, board_id=7, project_id=3,
        source_jid="120363@g.us", source_phone="120363", source_contact="Client",
        message_ids=[201],
    )
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_clear_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lifecycle,
        "record_verification_lesson",
        lambda **kwargs: {"recorded": True, "lesson": "redo with browser proof"},
    )
    gate = lifecycle.begin_post_completion_gates(
        ticket_id=9002, run_id=56, status="completed", result_summary="Maybe done",
    )
    rejected = lifecycle.handle_telegram_reply(f"wv:{gate['token']}:redo", chat_id=42)
    assert rejected["action"] == "verification_redo"
    assert lifecycle.prepare_completed_reply(
        ticket_id=9002, run_id=56, status="completed", result_summary="Maybe done",
    ) is None
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=9002"
        )).scalar_one() == "verification_rejected"
