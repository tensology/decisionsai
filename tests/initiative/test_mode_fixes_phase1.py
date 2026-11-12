"""Phase 1 initiative mode fixes. All sends and side effects are mocked."""

from unittest.mock import MagicMock

import pytest

import distr.core.initiative.work_scanner as work_scanner
from distr.core.initiative.policy import PolicyDecision, evaluate
from distr.core.initiative.proposed_action import ProposedAction
from distr.core.initiative.rubric import RubricScore
from distr.core.initiative.service import InitiativeService


def _service():
    service = InitiativeService.__new__(InitiativeService)
    service._cycle_lock = __import__("threading").Lock()
    service._cycle_running = False
    service._stopped = False
    service.telegram_manager = MagicMock(telegram_user_id=42)
    return service


# --- Quiet --------------------------------------------------------------

@pytest.mark.parametrize("action_type", ["suggestion", "board_triage", "workflow_start", "sensitive"])
def test_quiet_never_promotes_rubric_to_approval(action_type):
    action = ProposedAction(action_type=action_type, description="x", rubric=RubricScore(5, 5, 5, 5, 5))
    assert evaluate(action, "observe", {"initiative_allow_routine_tasks": True}) == PolicyDecision.SKIP


def test_quiet_idle_timer_does_not_start_a_cycle(monkeypatch):
    import distr.core.utils as utils

    monkeypatch.setattr(utils, "load_settings_from_db", lambda: {"initiative_level": "observe"})
    service = _service()
    service._dispatch_cycle = MagicMock()
    service._on_idle_timer_expired()
    service._dispatch_cycle.assert_not_called()


def test_quiet_cycle_never_calls_llm(monkeypatch):
    import distr.core.utils as utils

    monkeypatch.setattr(utils, "load_settings_from_db", lambda: {"initiative_level": "observe"})
    service = _service()
    for name, value in {
        "_last_cycle_success_at": None, "_last_cycle_at": None, "_cycle_count": 0,
        "_last_chat_stream_finished_at": None, "_last_cycle_error": "old",
    }.items():
        setattr(service, name, value)
    service._call_llm = MagicMock()
    service._context_assembler = MagicMock()
    service._draft_queue = MagicMock()
    service._record_cycle_success = MagicMock()
    service._record_cycle_failure = MagicMock()
    service._run_initiative_cycle("test")
    service._call_llm.assert_not_called()
    service._context_assembler.build.assert_not_called()
    service._record_cycle_success.assert_called_once()


def test_quiet_sends_no_execution_nudges():
    service = _service()
    service._send_telegram_if_allowed = MagicMock()
    service._maybe_send_execution_nudges({"initiative_level": "observe", "initiative_allow_telegram": True})
    service._send_telegram_if_allowed.assert_not_called()


# --- Telegram conversations toggle on intake cards ------------------------

def test_intake_card_respects_telegram_toggle(monkeypatch):
    import distr.core.utils as utils
    import distr.core.kanban.ticket_workflow_engagement as twe
    from distr.core.kanban.whatsapp_intake_approval import notify_telegram_approval

    manager = MagicMock()
    monkeypatch.setattr(twe, "_telegram_manager_from_app", lambda: manager)
    monkeypatch.setattr(utils, "load_settings_from_db", lambda: {"initiative_allow_telegram": False})
    assert notify_telegram_approval({"token": "t1", "text": "hello"}) is False
    manager.send_to_telegram.assert_not_called()

    monkeypatch.setattr(utils, "load_settings_from_db", lambda: {"initiative_allow_telegram": True})
    manager.send_to_telegram.return_value = True
    assert notify_telegram_approval({"token": "t1", "text": "hello"}) is True
    manager.send_to_telegram.assert_called_once()


# --- Email toggle -----------------------------------------------------------

def test_email_toggle_off_is_respected_even_when_gmail_connected(monkeypatch):
    scan_email = MagicMock()
    monkeypatch.setattr(work_scanner, "_gmail_connected", lambda: True)
    monkeypatch.setattr(work_scanner, "_tensology_mail_configured", lambda settings=None: True)
    monkeypatch.setattr(work_scanner, "_scan_email", scan_email)
    for name in ("_scan_local_boards", "_scan_external_boards", "_scan_whatsapp", "_scan_telegram",
                 "_scan_advanced_work_connectors", "_connected_work_sources"):
        monkeypatch.setattr(work_scanner, name, MagicMock(return_value=[]))
    base = {"initiative_scan_whatsapp": False, "initiative_scan_telegram": False}
    work_scanner.build_work_scan({**base, "initiative_scan_email": False})
    scan_email.assert_not_called()
    work_scanner.build_work_scan({**base, "initiative_scan_email": True})
    scan_email.assert_called_once()


# --- Level validation on save ---------------------------------------------

def _save_route(monkeypatch, saved):
    from fastapi import APIRouter
    import distr.core.settings as core_settings
    from distr.gui.web.routes.settings import initiative as route_mod

    monkeypatch.setattr(core_settings, "save_settings_to_db", lambda data: saved.append(dict(data)), raising=False)  # conftest stub is a MagicMock; a prior undo deletes the attr
    router = APIRouter()
    route_mod.register_routes(router, None)
    for route in router.routes:
        if route.path == "/initiative" and "POST" in route.methods:
            return route.endpoint, route_mod
    raise AssertionError("route missing")


def test_save_rejects_unknown_level_and_migrates_legacy(monkeypatch):
    import asyncio

    saved = []
    endpoint, mod = _save_route(monkeypatch, saved)
    bad = asyncio.run(endpoint(mod._InitiativePayload(initiative_level="standard")))
    assert bad.status_code == 400 and not saved
    ok = asyncio.run(endpoint(mod._InitiativePayload(initiative_level="autonomous")))
    assert ok.status_code == 200 and saved[-1]["initiative_level"] == "own"
