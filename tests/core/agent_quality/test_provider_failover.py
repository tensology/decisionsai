"""Reliability: every route fails over on provider-down errors.

The error strings are the real ones recorded in Decisions between 13 and 19
November 2026 (Ollama cloud 402, ollama.com DNS failure, OpenRouter batch id).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from distr.core import provider_failover as pf
from distr.core.turn_runtime import ModelRoute, TurnRequest, TurnScope
from distr.core.turn_runtime import service as turn_service

REAL_402 = (
    '402: {"message":"Add usage credits or subscribe and save up to 66%: https://ollama.com/upgrade '
    '(ref: 68c1b7dd)","type":"api_error","param":null,"code":null}'
)
REAL_CHAT_402 = (
    "Add usage credits or subscribe and save up to 66%: (ref: f286b403) (status code: 402)"
)
REAL_DNS = (
    '502 "Post \\"https://ollama.com:443/v1/chat/completions?ts=1794813170\\": '
    'dial tcp: lookup ollama.com: no such host"'
)
REAL_BATCH = (
    "deepseek/deepseek-v4.1-flash:batch cannot be used with the chat/completions endpoint "
    "(adapter DeepInfraBatchAdapter)"
)

# Mirrors Paul's settings shape: OpenRouter/OpenAI/Kilo/NVIDIA keyed, no backup set.
KEYED_SETTINGS = {
    "openrouter_key": "sk-test-openrouter",
    "openai_key": "sk-test-openai",
    "kilo_key": "kilo-test",
    "nvidia_key": "nv-test",
    "anthropic_key": "",
    "groq_key": "",
    "gemini_key": "",
    "conversational_llm_provider": "OpenRouter",
    "conversational_llm_model": "deepseek/deepseek-v4.1-flash",
    "llm_provider": "OpenRouter",
    "llm_model": "deepseek/deepseek-v4.1-flash",
    "automation_cli_model_provider": "openrouter",
    "automation_cli_model": "deepseek/deepseek-v4.1-flash",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(pf.FAILOVER_FILE_ENV, str(tmp_path / "missing.json"))
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "KILOCODE_API_KEY", "NVIDIA_API_KEY",
                 "GROQ_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    pf.reset_notice_state()
    yield
    pf.reset_notice_state()


@pytest.mark.parametrize(
    "text,category",
    [
        (REAL_402, "credits"),
        (REAL_CHAT_402, "credits"),
        ("Credit balance is too low", "credits"),
        (REAL_DNS, "unsupported_model" if False else "dns"),
        (REAL_BATCH, "unsupported_model"),
        ("HTTP 429 rate limit", "rate_limit"),
        ("RESOURCE_EXHAUSTED: quota exceeded", "quota"),
        ("Connection refused", "connection"),
    ],
)
def test_real_provider_errors_are_classified(text, category):
    assert pf.classify_provider_error(text) == category
    assert pf.is_failover_error(text)


@pytest.mark.parametrize(
    "text",
    [
        "Required verification has not passed. Inspect the recorded checks and rerun them.",
        "Stopped by the user.",
        "SyntaxError in views.py line 12",
        "",
    ],
)
def test_task_failures_are_not_provider_failures(text):
    assert pf.classify_provider_error(text) is None


def test_batch_and_non_chat_models_are_never_chat_capable():
    assert not pf.is_chat_capable_model("deepseek/deepseek-v4.1-flash:batch")
    assert not pf.is_chat_capable_model("text-embedding-3-small")
    assert not pf.is_chat_capable_model("whisper-1")
    assert not pf.is_chat_capable_model("auto")
    assert pf.is_chat_capable_model("deepseek/deepseek-v4.1-flash")
    assert pf.is_chat_capable_model("kimi-k3:cloud")


def test_ollama_cloud_402_fails_over_to_configured_openrouter_model():
    target = pf.next_failover("ollama", "deepseek-v4.1-flash:cloud", KEYED_SETTINGS, error=REAL_402)
    assert target == pf.FailoverTarget("openrouter", "deepseek/deepseek-v4.1-flash", "configured model")


def test_failed_provider_is_never_the_fallback_and_unkeyed_are_skipped():
    target = pf.next_failover("openrouter", "deepseek/deepseek-v4.1-flash", KEYED_SETTINGS, error=REAL_402)
    assert target is None or target.provider != "openrouter"
    settings = {**KEYED_SETTINGS, "openrouter_key": "", "openai_key": "", "kilo_key": "", "nvidia_key": ""}
    assert pf.next_failover("ollama", "kimi-k3:cloud", settings, error=REAL_402) is None


def test_batch_model_never_chosen_even_if_ordered(monkeypatch, tmp_path):
    order = tmp_path / "order.json"
    order.write_text('{"order": [{"provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash:batch"},'
                     ' {"provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash"}]}')
    monkeypatch.setenv(pf.FAILOVER_FILE_ENV, str(order))
    target = pf.next_failover("ollama", "kimi-k3:cloud", KEYED_SETTINGS, error=REAL_DNS)
    assert target.model == "deepseek/deepseek-v4.1-flash"


def test_batch_model_on_healthy_provider_switches_model_not_provider():
    target = pf.next_failover("openrouter", "deepseek/deepseek-v4.1-flash:batch", KEYED_SETTINGS, error=REAL_BATCH)
    assert target == pf.FailoverTarget("openrouter", "deepseek/deepseek-v4.1-flash", "configured model")


def test_default_order_exists_and_disable_switch_is_respected(monkeypatch, tmp_path):
    assert [item["provider"] for item in pf.DEFAULT_FAILOVER_ORDER][:2] == ["openrouter", "openai"]
    off = tmp_path / "off.json"
    off.write_text('{"enabled": false}')
    monkeypatch.setenv(pf.FAILOVER_FILE_ENV, str(off))
    assert pf.next_failover("ollama", "kimi-k3:cloud", KEYED_SETTINGS, error=REAL_402) is None


def test_notice_is_one_plain_sentence_and_rate_limited():
    target = pf.FailoverTarget("openrouter", "deepseek/deepseek-v4.1-flash")
    notice = pf.provider_down_notice("ollama", "kimi-k3:cloud", REAL_402, target)
    assert notice == (
        "Ollama (kimi-k3:cloud) is out of credits (402), so I switched to "
        "OpenRouter · deepseek/deepseek-v4.1-flash and carried on."
    )
    assert "{" not in notice and "ref:" not in notice
    none_left = pf.provider_down_notice("ollama", "kimi-k3:cloud", REAL_DNS, None)
    assert "Settings → LLMs" in none_left
    assert pf.should_emit_notice("ollama", "credits", now=1000.0)
    assert not pf.should_emit_notice("ollama", "credits", now=1000.0 + 60)
    assert pf.should_emit_notice("ollama", "credits", now=1000.0 + pf.NOTICE_COOLDOWN_SECONDS + 1)


# ------------------------------------------------------ Development routes --

ROUTES = {
    "development_thread": {"source": "development"},
    "scheduled_db_load": {"source": "development", "automation_id": "load-latest-spoortrack-db"},
    "work_planning_check": {"source": "development", "automation_id": "daily-work-planning"},
    "gmail_triage": {"source": "development", "automation_id": "email-action-items"},
}


def _route_request(metadata, *, provider="ollama", model="deepseek-v4.1-flash:cloud"):
    return TurnRequest(
        instruction="Run the job.",
        project=SimpleNamespace(id=7, folder_location="/tmp/project"),
        route=ModelRoute(provider=provider, model=model, backend_id="pi",
                         adapter_options={"model_provider": provider}),
        scope=TurnScope(chat_id=584, project_id=7, turn_id=1594),
        metadata=dict(metadata),
    )


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(pf, "_load_settings", lambda: dict(KEYED_SETTINGS))


@pytest.mark.parametrize("route_name", sorted(ROUTES))
@pytest.mark.parametrize(
    "error,start_model,start_provider",
    [
        (REAL_402, "deepseek-v4.1-flash:cloud", "ollama"),
        (REAL_DNS, "kimi-k3:cloud", "ollama"),
        (REAL_BATCH, "deepseek/deepseek-v4.1-flash:batch", "openrouter"),
    ],
)
def test_every_development_route_fails_over(monkeypatch, keyed, route_name, error, start_model, start_provider):
    calls, events = [], []

    class Runtime:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            calls.append((request.route.provider, request.route.model))
            if (request.route.provider, request.route.model) == (start_provider, start_model):
                return turn_service.TurnResult(success=False, runtime_id="cli_harness",
                                               backend_id="pi", model=start_model, error=error)
            return turn_service.TurnResult(success=True, runtime_id="cli_harness", backend_id="pi",
                                           model=request.route.model, output="done")

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "cli_harness")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: Runtime())
    request = _route_request(ROUTES[route_name], provider=start_provider, model=start_model)

    result = asyncio.run(turn_service.execute_turn(request, on_event=events.append))

    assert result.success is True
    assert calls == [(start_provider, start_model), ("openrouter", "deepseek/deepseek-v4.1-flash")]
    notices = [e for e in events if e.details.get("provider_down_notice")]
    assert len(notices) == 1
    assert "switched to OpenRouter · deepseek/deepseek-v4.1-flash" in notices[0].summary
    assert all(":batch" not in model for _, model in calls[1:])


def test_native_exception_402_continues_on_next_provider_not_same_provider_cli(monkeypatch, keyed):
    calls = []

    class Native:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            calls.append(("native", request.route.provider, request.route.model))
            if request.route.provider == "ollama":
                raise RuntimeError(REAL_402)
            return turn_service.TurnResult(success=True, runtime_id="native", backend_id="pi",
                                           model=request.route.model, output="ok")

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "native")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: Native())

    result = asyncio.run(turn_service.execute_turn(_route_request(ROUTES["development_thread"])))

    assert result.success
    assert calls[-1] == ("native", "openrouter", "deepseek/deepseek-v4.1-flash")


def test_chain_moves_past_a_second_dead_provider(monkeypatch, keyed):
    calls = []

    class Runtime:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            calls.append(request.route.provider)
            if request.route.provider in {"ollama", "openrouter"}:
                return turn_service.TurnResult(success=False, runtime_id="cli_harness", backend_id="pi",
                                               model=request.route.model, error=REAL_402)
            return turn_service.TurnResult(success=True, runtime_id="cli_harness", backend_id="pi",
                                           model=request.route.model, output="ok")

    settings = {**KEYED_SETTINGS, "coding_llm_provider": "openai", "coding_llm_model": "gpt-5.6-luna"}
    monkeypatch.setattr(pf, "_load_settings", lambda: settings)
    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "cli_harness")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: Runtime())

    result = asyncio.run(turn_service.execute_turn(_route_request(ROUTES["scheduled_db_load"])))

    assert result.success
    assert calls == ["ollama", "openrouter", "openai"]


def test_no_keyed_fallback_gives_one_clear_notice_not_raw_dump(monkeypatch):
    monkeypatch.setattr(pf, "_load_settings", lambda: {})

    class Runtime:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            return turn_service.TurnResult(success=False, runtime_id="cli_harness", backend_id="pi",
                                           model=request.route.model, error=REAL_402)

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "cli_harness")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: Runtime())

    result = asyncio.run(turn_service.execute_turn(_route_request(ROUTES["gmail_triage"])))

    assert result.success is False
    assert result.error.startswith("Ollama (deepseek-v4.1-flash:cloud) is out of credits (402)")
    assert "Settings → LLMs" in result.error
    assert result.evidence["provider_error"].startswith("402:")


def test_task_failure_does_not_fail_over(monkeypatch, keyed):
    calls = []

    class Runtime:
        async def execute(self, request, *, on_event=None, cancellation=None, steering=None):
            calls.append(request.route.provider)
            return turn_service.TurnResult(success=False, runtime_id="cli_harness", backend_id="pi",
                                           model=request.route.model,
                                           error="Required verification has not passed.")

    monkeypatch.setattr(turn_service, "select_turn_runtime_id", lambda request, runtime_id="": "cli_harness")
    monkeypatch.setattr(turn_service, "get_turn_runtime", lambda runtime_id, request=None: Runtime())
    asyncio.run(turn_service.execute_turn(_route_request(ROUTES["development_thread"])))
    assert calls == ["ollama"]


# --------------------------------------------------------------- chat route --

class _FakeChatManager:
    def __init__(self):
        self.saved = []

    def get_current_chat(self):
        return 1381

    def add_assistant_message(self, chat_id, text):
        self.saved.append((chat_id, text))


def _chat_service(model, provider_class_name="OllamaLLMService"):
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

    cls = type(provider_class_name, (LLMSharedMixin,), {})
    svc = cls.__new__(cls)
    svc._model_name = model
    svc._messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "Tell me a story."}]
    svc.chat_manager = _FakeChatManager()
    svc.event_queue = None
    svc._speaker_enabled = False
    svc._is_telegram_request = False
    frames = []

    async def push_frame(frame, direction=None):
        frames.append(frame)

    svc.push_frame = push_frame
    svc._frames = frames
    return svc


@pytest.mark.parametrize(
    "error,model,cls_name,expected",
    [
        (REAL_CHAT_402, "kimi-k3:cloud", "OllamaLLMService", ("openrouter", "deepseek/deepseek-v4.1-flash")),
        (REAL_DNS, "deepseek-v4.1-flash:cloud", "OllamaLLMService", ("openrouter", "deepseek/deepseek-v4.1-flash")),
        (REAL_BATCH, "deepseek/deepseek-v4.1-flash:batch", "OpenRouterLLMService",
         ("openrouter", "deepseek/deepseek-v4.1-flash")),
    ],
)
def test_chat_fails_over_and_replays_instead_of_showing_error(keyed, error, model, cls_name, expected):
    svc = _chat_service(model, cls_name)
    handed = {}

    def handler(**kwargs):
        handed.update(kwargs)
        return True

    svc._provider_failover_handler = handler
    shown = asyncio.run(svc._surface_model_error(RuntimeError(error)))

    assert shown == ""
    assert (handed["target"].provider, handed["target"].model) == expected
    assert handed["text"] == "Tell me a story."
    assert handed["chat_id"] == 1381
    assert len(svc.chat_manager.saved) == 1
    assert "switched to OpenRouter" in svc.chat_manager.saved[0][1]
    assert not svc._frames  # no raw ErrorFrame


def test_chat_batch_model_is_caught_before_calling_provider(keyed):
    svc = _chat_service("deepseek/deepseek-v4.1-flash:batch", "OpenRouterLLMService")
    handed = {}
    svc._provider_failover_handler = lambda **kw: handed.update(kw) or True
    called = {"generate": False}

    async def _generate_response():
        called["generate"] = True

    svc._generate_response = _generate_response
    asyncio.run(svc._surface_model_error(RuntimeError(
        "deepseek/deepseek-v4.1-flash:batch cannot be used with the chat/completions endpoint")))
    assert handed["target"].model == "deepseek/deepseek-v4.1-flash"
    assert called["generate"] is False


def test_chat_without_fallback_shows_plain_notice(monkeypatch):
    monkeypatch.setattr(pf, "_load_settings", lambda: {})
    svc = _chat_service("kimi-k3:cloud")
    svc._provider_failover_handler = lambda **kw: True
    shown = asyncio.run(svc._surface_model_error(RuntimeError(REAL_CHAT_402)))
    assert shown.startswith("Ollama (kimi-k3:cloud) is out of credits (402)")
    assert "Settings → LLMs" in shown


def test_session_failover_handler_persists_chat_model_and_replays(monkeypatch):
    from distr.core.agent import session as session_mod

    swaps, replays = [], []

    class FakeService:
        async def process_chat_input(self, text, **kwargs):
            replays.append((text, kwargs))

    fake = SimpleNamespace(logger=SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None))
    fake.llm_service = FakeService()

    def hot_swap(provider, model, chat_id):
        swaps.append((provider, model, chat_id))

    fake._hot_swap_llm_service = hot_swap

    async def main():
        ok = session_mod.AgentSession._on_provider_failover(
            fake, target=pf.FailoverTarget("openrouter", "deepseek/deepseek-v4.1-flash"),
            text="Tell me a story.", chat_id=None, notice="n", tried=[("Ollama", "kimi-k3:cloud")],
        )
        await asyncio.sleep(0.05)
        return ok

    assert asyncio.run(main()) is True
    assert swaps == [("OpenRouter", "deepseek/deepseek-v4.1-flash", None)]
    assert replays[0][0] == "Tell me a story."
    assert replays[0][1]["skip_user_persist"] is True
    assert fake.llm_service._failover_tried == [("Ollama", "kimi-k3:cloud")]
