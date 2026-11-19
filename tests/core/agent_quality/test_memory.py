"""Memory (area 5): owner identity, MemPalace recall visibility, nudge dedup.

Real cases:
  * Turn 1392 (15 Nov, Hayley Williams agent): "Hello, my name is Paul." →
    "Hey Paul, good to meet you."
  * Chat 1381: "Player1Sport (jira) has 76 fetched items available for review"
    posted 16 times between 15 and 19 Nov.
  * decisions.log on 19 Nov: MemPalace searched all day, zero "recall" lines.
"""

from __future__ import annotations

import logging

import pytest

from distr.core.agent.owner_identity import introduces_as_owner, owner_identity_block, owner_name
from distr.core.initiative import notice_dedup

NOTICE = ("I noticed something I can help with: Player1Sport (jira) has 76 fetched items available for "
          "review. Want me to take it from here?")


def _service(persona="You are Hayley Williams."):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

    svc = LLMSharedMixin.__new__(LLMSharedMixin)
    svc._persona = persona
    svc._username = "Paul"
    svc._build_system_prompt_template = lambda chat_id=None, include_tools_description=True: "BASE TEMPLATE"
    svc._get_provider_name = lambda: "openrouter"
    svc._injected_context_prompt = lambda chat_id=None: ""
    svc.get_session_reflection = lambda: ""
    return svc


def test_owner_is_pinned_right_after_any_agent_persona():
    message = _service()._build_system_message(chat_id=None)["content"]
    persona_at = message.index("You are Hayley Williams.")
    owner_at = message.index("## Owner")
    assert persona_at < owner_at < message.index("BASE TEMPLATE")
    assert "Paul owns this Decisions install" in message
    assert "never as a first meeting" in message
    assert 'never say "nice to meet you" or "good to meet you"' in message


def test_owner_block_survives_without_a_persona_and_for_local_models():
    svc = _service(persona=None)
    svc._get_provider_name = lambda: "ollama"
    svc._condense_for_local = lambda template: template[:5]
    message = svc._build_system_message(chat_id=None)["content"]
    assert message.startswith("## Owner")


def test_1392_my_name_is_paul_is_recognised_as_the_owner(monkeypatch):
    monkeypatch.delenv("DECISIONS_OWNER_NAME", raising=False)
    name = owner_name("Paul")
    assert name == "Paul"
    assert introduces_as_owner("Hello, my name is Paul.", name)
    assert introduces_as_owner("hey it's paul", name)
    assert not introduces_as_owner("My name is Sarah.", name)
    monkeypatch.setenv("DECISIONS_OWNER_NAME", "Paul Hoft von Hoesslin")
    assert owner_name("paul") == "Paul"
    assert owner_identity_block("") == ""
    assert owner_name("root") == "Paul"  # configured name wins over a generic OS account


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def core_log(monkeypatch):
    """Records from the core_mixin logger, even if an earlier test disabled
    or detached it (caplog relies on propagation to the root logger)."""
    log = logging.getLogger("distr.core.agent.services.llm.core_mixin")
    monkeypatch.setattr(log, "disabled", False)
    monkeypatch.setattr(log, "level", logging.INFO)
    monkeypatch.setattr(logging.root.manager, "disable", 0)
    handler = _Records()
    log.addHandler(handler)
    yield handler
    log.removeHandler(handler)


def test_mempalace_recall_injection_is_logged_at_info(monkeypatch, core_log):
    svc = _service()
    monkeypatch.setattr("distr.core.mempalace.wiring.prefer_read_rag_context",
                        lambda text: "Merrypak delivery uses R140 flat rate for Gauteng.")
    monkeypatch.setattr("distr.core.agent.services.llm.oracle_context.filter_rag_snippets",
                        lambda text, ctx: ctx)
    out = svc._maybe_inject_rag_context("What is the Merrypak delivery fee again?")
    assert "R140 flat rate" in out
    assert out.rstrip().endswith("What is the Merrypak delivery fee again?")
    hits = [r for r in core_log.records if "MemPalace RAG inject" in r.getMessage()]
    assert hits and hits[0].levelno == logging.INFO


def test_mempalace_recall_miss_is_logged_too(monkeypatch, core_log):
    svc = _service()
    monkeypatch.setattr("distr.core.mempalace.wiring.prefer_read_rag_context", lambda text: None)
    monkeypatch.setattr("distr.core.mempalace.wiring._enabled", lambda settings=None: True)
    svc._maybe_inject_rag_context("What did we decide about the Snuza dashboard?")
    assert any("MemPalace recall: no match" in r.getMessage() and r.levelno == logging.INFO for r in core_log.records)


def test_app_logging_writes_distr_info_to_the_log_file():
    """The file handler from distr.app.main keeps INFO for distr.* loggers."""
    import distr.app.main as app_main
    import inspect

    source = inspect.getsource(app_main)
    assert "file_handler.setLevel(logging.INFO)" in source
    assert "app_logger.setLevel(logging.INFO)" in source


@pytest.fixture
def dedup_file(tmp_path, monkeypatch):
    path = tmp_path / "dedup.json"
    monkeypatch.setenv("DECISIONS_NOTICE_DEDUP_FILE", str(path))
    return path


def test_chat_1381_same_nudge_is_posted_once_a_day(dedup_file):
    start = 1_763_000_000.0
    posted = [
        notice_dedup.should_post(f"{NOTICE} [last cycle {i}m ago; desktop: ChatGPT]", now=start + i * 2 * 3600)
        for i in range(12)  # every two hours, like 18–19 Nov
    ]
    assert posted == [True] + [False] * 11
    assert notice_dedup.should_post(NOTICE, now=start + 24 * 3600 + 1) is True  # next day


def test_changed_nudge_is_still_posted(dedup_file):
    assert notice_dedup.should_post(NOTICE, now=100.0)
    assert notice_dedup.should_post(NOTICE.replace("76", "81"), now=200.0)


def test_initiative_service_suppresses_the_repeat(dedup_file, monkeypatch):
    from distr.core.initiative.service import InitiativeService
    from distr.core.initiative.tiers import PermissionTier

    service = InitiativeService.__new__(InitiativeService)
    logged = []
    service._log_to_chat = lambda msg, settings: logged.append(msg)
    service._send_telegram_if_allowed = lambda *a, **k: None
    service._cycle_situational = {}
    action = type("A", (), {
        "action_type": "board_triage", "description": "Player1Sport (jira) has 76 fetched items available for review.",
        "draft": "", "telegram_message": "", "payload": {}, "suggested_tool": None,
    })()
    for _ in range(3):
        service._deliver_suggestion(action, {}, tier=PermissionTier.SUGGEST if hasattr(PermissionTier, "SUGGEST") else None)
    assert len(logged) == 1
