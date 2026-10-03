"""Board-linked WA intake surface: announce + TG track; quiet unless asked."""

from __future__ import annotations

from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def rules_db(monkeypatch, tmp_path):
    import distr.core.kanban.whatsapp_intake_rules as rules

    engine = create_engine(f"sqlite:///{tmp_path / 'wa_intake_rules.db'}")
    monkeypatch.setattr(rules, "engine", engine)
    rules.ensure_telegram_push_table()
    return rules


def test_unlinked_message_is_silent_when_board_linked_mode(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "board_linked")
    monkeypatch.setattr(
        rules,
        "resolve_whatsapp_link",
        lambda jid, jid_phone="": rules.WhatsAppLinkInfo(phone_jid=jid),
    )

    decision = rules.decide_intake_surface(jid="27630000000@s.whatsapp.net")
    assert decision.linked is False
    assert decision.should_announce is False
    assert decision.should_telegram_push is False
    assert decision.should_route_work_intake is False
    assert decision.should_inject_message_bus is False
    assert decision.reason == "project_unlinked_silent"


def test_resolve_whatsapp_link_requires_existing_board_project(monkeypatch, tmp_path):
    import distr.core.db as db_module
    import distr.core.kanban.whatsapp_intake_rules as rules
    from distr.core.db import WhatsAppPhoneLink
    from distr.core.db.kanban import KanbanBoard
    from distr.core.db.projects import Project

    engine = create_engine(f"sqlite:///{tmp_path / 'wa_project_link.db'}")
    Project.__table__.create(engine)
    KanbanBoard.__table__.create(engine)
    WhatsAppPhoneLink.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(db_module, "get_session", session_factory)

    with session_factory() as session:
        session.add(Project(id=13, name="AuctionNow", folder_location="/tmp/auction"))
        session.add(KanbanBoard(id=3, name="AuctionNow", default_project_id=13))
        session.add(KanbanBoard(id=8, name="Orphaned", default_project_id=99))
        session.add(WhatsAppPhoneLink(
            id=1,
            board_id=3,
            phone_jid="27630000001@s.whatsapp.net",
            phone_number="27630000001",
            contact_name="Linked",
            auto_snapshot=False,
        ))
        session.add(WhatsAppPhoneLink(
            id=2,
            board_id=8,
            phone_jid="27630000002@s.whatsapp.net",
            phone_number="27630000002",
            contact_name="Orphaned",
            auto_snapshot=False,
        ))
        session.commit()

    linked = rules.resolve_whatsapp_link("27630000001@s.whatsapp.net")
    orphaned = rules.resolve_whatsapp_link("27630000002@s.whatsapp.net")

    assert (linked.board_id, linked.project_id) == (3, 13)
    assert (orphaned.board_id, orphaned.project_id) == (8, None)


@pytest.mark.parametrize("surface_mode", ["legacy", "board_linked"])
def test_board_link_without_project_is_silent(monkeypatch, rules_db, surface_mode):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", surface_mode)
    link = rules.WhatsAppLinkInfo(
        board_id=8,
        project_id=None,
        board_name="Unlinked board",
        contact_name="Contact",
        phone_jid="27630000001@s.whatsapp.net",
    )

    decision = rules.decide_intake_surface(jid=link.phone_jid, link=link)

    assert decision.linked is False
    assert decision.board_id == 8
    assert decision.should_announce is False
    assert decision.should_telegram_push is False
    assert decision.should_route_work_intake is False
    assert decision.should_inject_message_bus is False
    assert decision.reason == "project_unlinked_silent"


def test_project_linked_message_waits_for_grouped_work_scan(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "board_linked")
    link = rules.WhatsAppLinkInfo(
        board_id=7,
        project_id=17,
        board_name="AuctionNow",
        contact_name="Client",
        phone_jid="27631111111@s.whatsapp.net",
        auto_snapshot=False,
        link_id=1,
    )
    decision = rules.decide_intake_surface(jid=link.phone_jid, link=link)
    assert decision.linked is True
    assert decision.should_announce is False
    assert decision.should_telegram_push is False
    assert decision.should_inject_message_bus is False
    assert decision.should_route_work_intake is False
    assert decision.reason == "project_linked_queued_for_batch_scan"


def test_linked_telegram_push_failure_is_recorded(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "board_linked")
    monkeypatch.delenv("DECISIONSAI_WHATSAPP_DRY_RUN", raising=False)
    link = rules.WhatsAppLinkInfo(
        board_id=9,
        project_id=19,
        board_name="Tensology",
        contact_name="Ops",
        phone_jid="27632222222@s.whatsapp.net",
        link_id=2,
    )
    decision = rules.IntakeSurfaceDecision(
        surface_mode=rules.SURFACE_BOARD_LINKED,
        linked=True,
        board_id=9,
        board_name="Tensology",
        contact_name="Ops",
        should_announce=False,
        should_telegram_push=True,
        should_route_work_intake=True,
        should_inject_message_bus=False,
        reason="board_linked_announce_and_telegram",
        link=link,
    )
    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: None,
    )
    result = rules.push_board_linked_to_telegram(
        decision,
        preview="hello",
        source_message_id="wa-fail",
        jid=link.phone_jid,
    )
    assert result.attempted is True
    assert result.ok is False
    assert result.error == "telegram_manager_unavailable"
    failed = rules.list_failed_telegram_pushes(limit=5)
    assert any(row["source_message_id"] == "wa-fail" for row in failed)


def test_normal_wa_quiet_unless_explicit_read_ask():
    from distr.core.kanban.whatsapp_intake_rules import is_explicit_whatsapp_read_request

    assert is_explicit_whatsapp_read_request(
        "any new messages come in, please read them to me"
    )
    assert is_explicit_whatsapp_read_request("Read my WhatsApp messages")
    assert is_explicit_whatsapp_read_request("check my whatsapp")
    assert not is_explicit_whatsapp_read_request("thanks for the update")
    assert not is_explicit_whatsapp_read_request("create a ticket for checkout")


def test_route_unlinked_skips_announce_and_bus(monkeypatch, rules_db):
    """Manager route: unlinked under board_linked mode never injects MessageBus."""
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "board_linked")
    monkeypatch.setenv("DECISIONSAI_WHATSAPP_DRY_RUN", "1")

    import distr.core.integrations.whatsapp.manager as wa_manager

    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.resolve_whatsapp_link",
        lambda jid, jid_phone="": rules.WhatsAppLinkInfo(phone_jid=jid),
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.decide_intake_surface",
        rules.decide_intake_surface,
    )

    bus_calls = []
    work_calls = []
    announce_calls = []

    class FakeBus:
        def ingest_incoming(self, msg):
            bus_calls.append(msg)
            return True

    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: FakeBus(),
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.apply_board_linked_surface_actions",
        lambda *a, **k: announce_calls.append(k) or {},
    )

    # Avoid importing WorkIntake if route short-circuits correctly.
    def boom(*a, **k):
        work_calls.append(1)
        raise AssertionError("WorkIntake should not run for unlinked silent")

    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service",
        boom,
        raising=False,
    )

    wa_manager._route_whatsapp_text_to_message_bus(
        {
            "jid": "27639999999@s.whatsapp.net",
            "jid_phone": "27639999999",
            "text": "Hi Philippa here",
            "message_id": "wa-unlinked-1",
            "sender": {"phone": "27639999999", "push_name": "Philippa"},
            "from_me": False,
        },
        "[WhatsApp: Philippa] Hi Philippa here",
        "27639999999@s.whatsapp.net",
    )
    assert bus_calls == []
    assert announce_calls == []
    assert work_calls == []


def test_route_project_linked_queues_for_batch_without_immediate_actions(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "board_linked")
    monkeypatch.setenv("DECISIONSAI_WHATSAPP_DRY_RUN", "1")

    import distr.core.integrations.whatsapp.manager as wa_manager

    link = rules.WhatsAppLinkInfo(
        board_id=3,
        project_id=13,
        board_name="AuctionNow",
        contact_name="Bidder",
        phone_jid="27634444444@s.whatsapp.net",
        auto_snapshot=False,
        link_id=4,
    )

    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.resolve_whatsapp_link",
        lambda *a, **k: link,
    )

    bus_calls = []
    tracking_calls = []
    ingest_calls = []

    class FakeBus:
        def ingest_incoming(self, msg):
            bus_calls.append(msg)
            return True

    class FakeDecision:
        handled = True
        action = SimpleNamespace(value="request_approval")
        ticket_id = None
        workflow_run_id = None

    class FakeService:
        def ingest(self, intake):
            ingest_calls.append(intake)
            return FakeDecision()

    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: FakeBus(),
    )
    monkeypatch.setattr(
        "distr.core.work_intake.get_work_intake_service",
        lambda: FakeService(),
    )
    # WorkIntake import path uses package; patch inside route via module import.
    import distr.core.work_intake as work_intake_pkg

    monkeypatch.setattr(work_intake_pkg, "get_work_intake_service", lambda: FakeService())

    def fake_apply(decision, **kwargs):
        tracking_calls.append({"decision": decision, **kwargs})
        return {
            "announced": True,
            "telegram_attempted": True,
            "telegram_ok": True,
            "telegram_error": "",
            "telegram_push_id": 1,
            "telegram_dry_run": True,
            "reason": decision.reason,
            "board_id": decision.board_id,
        }

    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.apply_board_linked_surface_actions",
        fake_apply,
    )

    wa_manager._route_whatsapp_text_to_message_bus(
        {
            "jid": link.phone_jid,
            "jid_phone": "27634444444",
            "text": "Create a ticket: payout retry",
            "message_id": "wa-linked-1",
            "sender": {"phone": "27634444444", "push_name": "Bidder"},
            "from_me": False,
        },
        "[WhatsApp: Bidder] Create a ticket: payout retry",
        link.phone_jid,
    )

    assert tracking_calls == []
    assert bus_calls == []
    assert ingest_calls == []


def test_legacy_mode_keeps_project_unlinked_message_silent(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "legacy")

    import distr.core.integrations.whatsapp.manager as wa_manager
    import distr.core.work_intake as work_intake_pkg

    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.resolve_whatsapp_link",
        lambda *a, **k: rules.WhatsAppLinkInfo(phone_jid="x"),
    )

    bus_calls = []

    class FakeBus:
        def ingest_incoming(self, msg):
            bus_calls.append(msg)
            return True

    class FakeDecision:
        handled = False
        action = SimpleNamespace(value="answer_directly")
        ticket_id = None
        workflow_run_id = None

    class FakeService:
        def ingest(self, intake):
            return FakeDecision()

    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: FakeBus(),
    )
    monkeypatch.setattr(work_intake_pkg, "get_work_intake_service", lambda: FakeService())

    wa_manager._route_whatsapp_text_to_message_bus(
        {
            "jid": "27635555555@s.whatsapp.net",
            "jid_phone": "27635555555",
            "text": "hey",
            "message_id": "wa-legacy-1",
            "sender": {"phone": "27635555555"},
            "from_me": False,
        },
        "[WhatsApp: 27635555555] hey",
        "27635555555@s.whatsapp.net",
    )
    assert bus_calls == []


def test_legacy_mode_queues_project_linked_message_for_batch(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.setenv("DECISIONSAI_WA_INTAKE_SURFACE", "legacy")

    import distr.core.integrations.whatsapp.manager as wa_manager
    import distr.core.work_intake as work_intake_pkg

    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_intake_rules.resolve_whatsapp_link",
        lambda *a, **k: rules.WhatsAppLinkInfo(
            board_id=3,
            project_id=13,
            board_name="AuctionNow",
            phone_jid="x",
        ),
    )

    bus_calls = []

    class FakeBus:
        def ingest_incoming(self, msg):
            bus_calls.append(msg)
            return True

    class FakeDecision:
        handled = False
        action = SimpleNamespace(value="answer_directly")
        ticket_id = None
        workflow_run_id = None

    class FakeService:
        def ingest(self, intake):
            return FakeDecision()

    monkeypatch.setattr(
        "distr.core.integrations.bus.get_integration_message_bus",
        lambda: FakeBus(),
    )
    monkeypatch.setattr(work_intake_pkg, "get_work_intake_service", lambda: FakeService())

    wa_manager._route_whatsapp_text_to_message_bus(
        {
            "jid": "27635555555@s.whatsapp.net",
            "jid_phone": "27635555555",
            "text": "create a project ticket",
            "message_id": "wa-legacy-linked-1",
            "sender": {"phone": "27635555555"},
            "from_me": False,
        },
        "[WhatsApp: 27635555555] create a project ticket",
        "27635555555@s.whatsapp.net",
    )
    assert bus_calls == []


def test_board_policy_keeps_project_linked_message_in_batch_queue(monkeypatch, rules_db):
    rules = rules_db
    monkeypatch.delenv("DECISIONSAI_WA_INTAKE_SURFACE", raising=False)
    link = rules.WhatsAppLinkInfo(
        board_id=11,
        project_id=21,
        board_name="Tensology",
        contact_name="Mailshot",
        phone_jid="27636666666@s.whatsapp.net",
        link_id=5,
    )
    board = SimpleNamespace(
        name="Tensology",
        orchestrator_policy='{"whatsapp_intake": {"mode": "board_linked_quiet"}}',
    )
    decision = rules.decide_intake_surface(
        jid=link.phone_jid, link=link, board=board
    )
    assert decision.surface_mode == rules.SURFACE_BOARD_LINKED
    assert decision.should_announce is False
    assert decision.should_telegram_push is False
    assert decision.should_inject_message_bus is False
    assert decision.should_route_work_intake is False
    assert decision.reason == "project_linked_queued_for_batch_scan"
