"""Local dry-run: WA classify → TG yes/no/add → fold → readiness → ticket create."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy import create_engine, text

from distr.core.kanban import whatsapp_intake_approval as wi
from distr.core.work_intake import WorkIntake, WorkIntakeAction, OrchestratorIntakeService


def _iso(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wa_intake.db'}")
    monkeypatch.setattr(wi, "engine", engine)
    wi.ensure_tables()
    return engine


def test_readiness_phrases_and_markup():
    labels = [b["text"] for b in wi.intake_markup("tok")["inline_keyboard"][0]]
    assert labels == ["Yes", "No", "Add"]
    assert "ready" in wi.READINESS_PHRASES
    assert "build it" in wi.READINESS_PHRASES
    assert "create ticket" in wi.READINESS_PHRASES


def test_wa_classify_stages_tg_yes_no_add_then_ready_creates_ticket(monkeypatch, tmp_path):
    """Furthest local dry-run without live Paul Telegram presses."""
    engine = _iso(monkeypatch, tmp_path)
    monkeypatch.setenv("DECISIONSAI_WHATSAPP_INTAKE_APPROVAL", "1")
    monkeypatch.setenv("DECISIONSAI_WHATSAPP_DRY_RUN", "1")

    sent = []

    class FakeManager:
        def send_to_telegram(self, message, reply_markup=None):
            sent.append({"text": message, "reply_markup": reply_markup})
            return True

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: FakeManager(),
    )

    # Avoid durable board/ticket DB — prove gate + fold + readiness handoff.
    created = {}

    def fake_create(self, intake, decision):
        decision.ticket_id = 4242
        decision.board_id = 7
        decision.project_id = 3
        decision.workflow_id = 11
        decision.handled = True
        decision.status = "ticket_created"
        decision.response_text = "Created ticket #4242"
        created["intake_text"] = intake.text
        created["meta"] = dict(intake.metadata or {})

    def fake_start(self, intake, decision):
        decision.workflow_run_id = 555
        decision.status = "workflow_started"
        decision.diagnostics["development_chat_id"] = 909
        decision.response_text = "Created ticket #4242 and started workflow run #555."
        created["started"] = True

    monkeypatch.setattr(OrchestratorIntakeService, "_create_ticket", fake_create)
    monkeypatch.setattr(OrchestratorIntakeService, "_start_workflow", fake_start)
    monkeypatch.setattr(OrchestratorIntakeService, "_start_lightweight_execution", Mock())
    monkeypatch.setattr(
        OrchestratorIntakeService,
        "_log_decision",
        staticmethod(lambda *a, **k: None),
    )

    # Fresh decision each classify() — return_value would mutate across stage→ready.
    from distr.core.work_intake.contracts import WorkIntakeDecision

    def _classify(_self, _intake):
        return WorkIntakeDecision(
            WorkIntakeAction.CREATE_TICKET,
            "test",
            diagnostics={"execute_lightweight": True},
        )

    # Reset singleton so readiness ingest uses patched class methods.
    import distr.core.work_intake.service as intake_service

    intake_service._service = None

    # 1) WA message → classify → stage (no ticket yet)
    intake = WorkIntake(
        source="whatsapp",
        user_text="Create a ticket: fix the checkout button on mobile",
        source_message_id="wa-dry-1",
        source_thread_id="120363@g.us",
        source_user_id="Client",
        metadata={"jid": "120363@g.us", "jid_phone": "120363", "message_ids": [1]},
    )
    with patch.object(OrchestratorIntakeService, "classify", autospec=True, side_effect=_classify):
        decision = OrchestratorIntakeService().ingest(intake)

        assert decision.action == WorkIntakeAction.REQUEST_APPROVAL
        assert decision.status == "pending_approval"
        assert decision.ticket_id is None
        token = decision.diagnostics["whatsapp_intake_token"]
        assert token
        assert len(sent) == 1
        assert "Yes" in [b["text"] for b in sent[0]["reply_markup"]["inline_keyboard"][0]]
        assert "ready" in sent[0]["text"].lower()

        # 2) Add fold
        folded = wi.handle_telegram_reply(f"wi:{token}:add", chat_id=42)
        assert folded["action"] == "add_prompt"
        folded2 = wi.handle_telegram_reply("include the iOS Safari repro steps", chat_id=42)
        assert folded2["action"] == "add"
        assert "iOS Safari" in folded2["text"]

        # 3) Readiness → ticket + workflow start (Dev thread / time via start_workflow path)
        ready = wi.handle_telegram_reply("build it", chat_id=42)
        assert ready["action"] == "ready"
        assert ready["ticket_id"] == 4242
        assert ready["workflow_run_id"] == 555
        assert ready["development_chat_id"] == 909
        assert created.get("started") is True
        assert created["meta"].get("intake_approval_confirmed") is True
        assert "iOS Safari" in created["intake_text"]

        with engine.connect() as conn:
            status = conn.execute(
                text("SELECT status FROM whatsapp_intake_approvals WHERE token=:t"),
                {"t": token},
            ).scalar_one()
        assert status == "created"


def test_plain_add_prefix_and_no_discard(monkeypatch, tmp_path):
    engine = _iso(monkeypatch, tmp_path)
    pending = wi.stage_whatsapp_intake(
        intake_payload={"source": "whatsapp", "user_text": "Create a ticket: paint the bike shed"},
        classified_action="create_ticket",
        classified_reason="test",
        draft_text="Create a ticket: paint the bike shed",
    )
    token = pending["token"]
    # Bind chat by simulating notify path update via fold
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE whatsapp_intake_approvals SET telegram_chat_id='99' WHERE token=:t"),
            {"t": token},
        )
    folded = wi.handle_telegram_reply("add use matte black", chat_id=99)
    assert folded["action"] == "add"
    discarded = wi.handle_telegram_reply("no", chat_id=99)
    assert discarded["action"] == "no"
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM whatsapp_intake_approvals WHERE token=:t"),
            {"t": token},
        ).scalar_one()
    assert status == "discarded"


def test_add_context_prefix_and_voice_style_ready(monkeypatch, tmp_path):
    """Voice notes become plain text after STT; same readiness/add phrases apply."""
    engine = _iso(monkeypatch, tmp_path)
    pending = wi.stage_whatsapp_intake(
        intake_payload={"source": "whatsapp", "user_text": "Create a ticket: ship the menu"},
        classified_action="create_ticket",
        draft_text="Create a ticket: ship the menu",
    )
    token = pending["token"]
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE whatsapp_intake_approvals SET telegram_chat_id='7' WHERE token=:t"),
            {"t": token},
        )
    folded = wi.handle_telegram_reply("add context: vegetarian options only", chat_id=7)
    assert folded["action"] == "add"
    assert "vegetarian" in folded["text"].lower()

    created = {}

    def fake_create(self, intake, decision):
        decision.ticket_id = 77
        decision.workflow_id = 1
        decision.handled = True
        decision.status = "ticket_created"
        created["text"] = intake.text

    def fake_start(self, intake, decision):
        decision.workflow_run_id = 88
        decision.diagnostics["development_chat_id"] = 99
        decision.status = "workflow_started"

    from distr.core.work_intake.contracts import WorkIntakeDecision
    import distr.core.work_intake.service as intake_service

    intake_service._service = None
    monkeypatch.setattr(OrchestratorIntakeService, "_create_ticket", fake_create)
    monkeypatch.setattr(OrchestratorIntakeService, "_start_workflow", fake_start)
    monkeypatch.setattr(OrchestratorIntakeService, "_start_lightweight_execution", Mock())
    monkeypatch.setattr(
        OrchestratorIntakeService,
        "_log_decision",
        staticmethod(lambda *a, **k: None),
    )

    def _classify(_self, _intake):
        return WorkIntakeDecision(
            WorkIntakeAction.CREATE_TICKET,
            "test",
            diagnostics={"execute_lightweight": True},
        )

    with patch.object(OrchestratorIntakeService, "classify", autospec=True, side_effect=_classify):
        # Simulated voice transcript saying readiness phrase
        ready = wi.handle_telegram_reply("ready", chat_id=7)
    assert ready["action"] == "ready"
    assert ready["ticket_id"] == 77
    assert "vegetarian" in created["text"].lower()

