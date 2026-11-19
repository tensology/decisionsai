"""Edge cases for the agent-quality work: failover, star, spoken cap, fast actions, memory."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from distr.core import provider_failover as pf

from .conftest import run_isolated

ROOT = Path(__file__).resolve().parents[3]
KEYED = {
    "openrouter_key": "sk-test-openrouter",
    "openai_key": "sk-test-openai",
    "kilo_key": "kilo-test",
    "nvidia_key": "nv-test",
    "conversational_llm_provider": "OpenRouter",
    "conversational_llm_model": "deepseek/deepseek-v4.1-flash",
    "llm_provider": "OpenRouter",
    "llm_model": "deepseek/deepseek-v4.1-flash",
}
ENV_KEYS = ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "KILOCODE_API_KEY", "NVIDIA_API_KEY",
            "GROQ_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(pf.FAILOVER_FILE_ENV, str(tmp_path / "missing.json"))
    for name in ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    pf.reset_notice_state()
    yield


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    from distr.core.services import model_catalog_cache

    cat = tmp_path / "catalog"
    cat.mkdir()
    (cat / "openai_a.json").write_text(json.dumps({"models": [{"id": "gpt-5.4-mini"}]}))
    (cat / "kilocode_b.json").write_text(json.dumps({"models": [{"id": "deepseek/deepseek-v4.1-flash"}]}))
    (cat / "nvidia_c.json").write_text(json.dumps({"models": [{"id": "deepseek-ai/deepseek-v4.1-flash"}]}))
    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", str(cat))
    return cat


# ------------------------------------------------------------------ failover --

def test_all_providers_down_walks_each_backup_once_then_stops(catalog):
    tried, seen = [], []
    provider, model = "openrouter", "deepseek/deepseek-v4.1-flash"
    for _ in range(20):
        target = pf.next_failover(provider, model, KEYED, tried=tried, error="503 service unavailable")
        if target is None:
            break
        key = (target.provider, target.model.lower())
        assert key not in seen, "a dead route was offered twice"
        seen.append(key)
        tried.append((provider, model))
        provider, model = target.provider, target.model
    else:
        pytest.fail("failover never ran out of routes")
    assert len(seen) >= 2
    assert ("openrouter", "deepseek/deepseek-v4.1-flash") not in seen


def test_all_providers_down_gives_one_plain_notice_without_raw_dump():
    notice = pf.provider_down_notice("openrouter", "deepseek/deepseek-v4.1-flash", "503 service unavailable", None)
    assert notice and "\n" not in notice.strip()
    assert "{" not in notice and "Traceback" not in notice


def test_partial_keys_env_only_key_counts_and_blank_key_does_not(monkeypatch, catalog):
    settings = {**KEYED, "openai_key": "   ", "kilo_key": "", "nvidia_key": ""}
    got = [t.provider for t in pf.failover_candidates("openrouter", "deepseek/deepseek-v4.1-flash", settings)]
    assert got == []  # whitespace is not a key
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-env")
    got = [t.provider for t in pf.failover_candidates("openrouter", "deepseek/deepseek-v4.1-flash", settings)]
    assert got == ["nvidia"]


def test_unknown_provider_name_is_never_a_target(monkeypatch, tmp_path):
    order = tmp_path / "order.json"
    order.write_text(json.dumps({"order": [{"provider": "madeup", "model": "x-1"}, "also-fake"]}))
    monkeypatch.setenv(pf.FAILOVER_FILE_ENV, str(order))
    assert pf.next_failover("openrouter", "deepseek/deepseek-v4.1-flash", KEYED, error="503") is None


def test_corrupt_failover_file_falls_back_to_defaults(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv(pf.FAILOVER_FILE_ENV, str(bad))
    assert pf.load_failover_order() == [dict(i) for i in pf.DEFAULT_FAILOVER_ORDER]


class _Status(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


def _stream_service(chunks_before_error, error):
    from distr.core.agent.services.llm.openai_compat import OpenAICompatibleLLMService

    calls = {"create": 0}

    async def create(**kwargs):
        calls["create"] += 1

        async def gen():
            for i in range(chunks_before_error):
                yield SimpleNamespace(i=i)
            raise error

        return gen()

    svc = OpenAICompatibleLLMService.__new__(OpenAICompatibleLLMService)
    svc.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    svc._model_name = "m"
    svc.SERVICE_NAME = "OpenRouterLLMService"
    return svc, calls


def _drain(svc):
    got = []

    async def run():
        async for chunk in svc._iter_stream_with_retry([{"role": "user", "content": "hi"}]):
            got.append(chunk)

    return got, run


def test_402_mid_stream_is_raised_once_and_classified_for_failover():
    err = _Status(402, "Add usage credits or subscribe (status code: 402)")
    svc, calls = _stream_service(2, err)
    got, run = _drain(svc)
    with pytest.raises(_Status):
        run_isolated(run())
    assert len(got) == 2 and calls["create"] == 1  # no replay of half an answer on the dead provider
    assert pf.classify_provider_error(err) == "credits" and pf.is_failover_error(err)


def test_402_before_first_chunk_is_not_retried_on_the_same_provider():
    err = _Status(402, "402: payment required")
    svc, calls = _stream_service(0, err)
    _, run = _drain(svc)
    with pytest.raises(_Status):
        run_isolated(run())
    assert calls["create"] == 1


@pytest.mark.parametrize("text,category", [
    ("Request timed out while resolving: lookup api.openrouter.ai: no such host", "dns"),
    ("ReadTimeout: timed out", "connection"),
    ("getaddrinfo failed after timeout", "dns"),
])
def test_dns_and_timeout_combinations_fail_over(text, category):
    assert pf.classify_provider_error(text) == category
    assert pf.is_failover_error(text)


def test_dns_then_timeout_chain_ends_without_repeating(catalog):
    first = pf.next_failover("openrouter", "deepseek/deepseek-v4.1-flash", KEYED,
                             error="lookup openrouter.ai: no such host")
    second = pf.next_failover(first.provider, first.model, KEYED,
                              tried=[("openrouter", "deepseek/deepseek-v4.1-flash")], error="timed out")
    assert first and second and (first.provider, first.model) != (second.provider, second.model)
    assert second.provider not in ("openrouter", first.provider)


def test_batch_only_catalog_offers_no_backup_for_that_provider(tmp_path, monkeypatch):
    from distr.core.services import model_catalog_cache

    cat = tmp_path / "batch"
    cat.mkdir()
    (cat / "kilocode_z.json").write_text(json.dumps({"models": [
        {"id": "deepseek/deepseek-v4.1-flash:batch"}, {"id": "text-embedding-3-large"}]}))
    monkeypatch.setattr(model_catalog_cache, "MODEL_CATALOG_CACHE_DIR", str(cat))
    assert pf.catalog_fallback_models("kilocode", "deepseek/deepseek-v4.1-flash:batch") == []
    settings = {**KEYED, "openai_key": "", "nvidia_key": ""}
    got = pf.failover_candidates("openrouter", "deepseek/deepseek-v4.1-flash", settings)
    assert all(pf.is_chat_capable_model(t.model) for t in got)
    assert not any(t.provider == "kilocode" for t in got)


def test_user_cancel_is_never_treated_as_provider_down():
    for text in ("Cancelled by the user", "503 but stopped by the user", ""):
        assert not pf.is_failover_error(text)
    assert not pf.is_failover_error(None)


# ------------------------------------------------------- star / loaded agent --

def _sync(chat_id, agent):
    from distr.core.chat_agents import sync_primary_to_live_chat

    primary_row = SimpleNamespace(agent_id=3)
    session = MagicMock()
    session.get.side_effect = lambda model, key: primary_row if key == 1 else None
    with patch("distr.core.chat_agents.get_session") as gs, patch(
        "distr.core.chat_agents.agent_for_conversation", return_value=agent
    ), patch("distr.core.chat_agents.primary_agent_payload",
             side_effect=lambda *a, **k: {"agent_id": getattr(agent, "id", None)}):
        gs.return_value.__enter__.return_value = session
        payload = sync_primary_to_live_chat(chat_id)
    return payload, primary_row, session


def test_rapid_star_switching_ends_on_the_last_agent():
    from distr.core.chat_agents import sync_primary_to_live_chat

    agents = {11: SimpleNamespace(id=7, name="A", skin_name="oracle", main_chat_id=11, oracle_background=None),
              12: SimpleNamespace(id=8, name="B", skin_name="oracle", main_chat_id=12, oracle_background=None)}
    primary_row = SimpleNamespace(agent_id=3)
    session = MagicMock()
    session.get.side_effect = lambda model, key: primary_row if key == 1 else None
    with patch("distr.core.chat_agents.get_session") as gs, patch(
        "distr.core.chat_agents.agent_for_conversation", side_effect=lambda session, cid: agents.get(int(cid))
    ), patch("distr.core.chat_agents.primary_agent_payload", side_effect=lambda *a, **k: {}):
        gs.return_value.__enter__.return_value = session
        for cid in (11, 12, 11, 12, 12, 11):
            sync_primary_to_live_chat(cid)
    assert primary_row.agent_id == 7


def test_star_on_deleted_chat_changes_nothing():
    payload, primary_row, session = _sync(999, None)
    assert payload is None and primary_row.agent_id == 3
    session.commit.assert_not_called()


@pytest.mark.parametrize("bad", [None, 0, -4, "abc", "", 1.5j])
def test_star_ignores_invalid_chat_ids(bad):
    from distr.core.chat_agents import sync_primary_to_live_chat

    with patch("distr.core.chat_agents.get_session") as gs:
        assert sync_primary_to_live_chat(bad) is None
    gs.assert_not_called()


def _chat_js():
    return (ROOT / "distr/gui/web/static/chat/js/chat.js").read_text(encoding="utf-8")


def _agent_changed_block():
    src = _chat_js()
    block = src.split("if (msg.type === 'agent_chat_changed') {", 1)[1].split("if (msg.type === 'chat_updated'", 1)[0]
    return block.rsplit("}", 1)[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to run the real chat.js handler")
def test_agent_chat_changed_handler_behaviour_in_node():
    """Run the real handler: valid ids move star+loaded; null/garbage ids never clear them."""
    script = """
let loadedChatId = 5, agentCurrentChatId = 5, painted = 0, reloads = 0;
function updateActiveChat() { painted++; }
function loadChats() { reloads++; return Promise.resolve(); }
function handle(msg) { %s }
const out = [];
handle({agent_current_chat_id: 9}); out.push([loadedChatId, agentCurrentChatId]);
handle({agent_current_chat_id: null}); out.push([loadedChatId, agentCurrentChatId]);
handle({agent_current_chat_id: 'abc'}); out.push([loadedChatId, agentCurrentChatId]);
handle({agent_current_chat_id: '12'}); out.push([loadedChatId, agentCurrentChatId]);
for (const id of [1,2,3,4,12]) handle({agent_current_chat_id: id});
out.push([loadedChatId, agentCurrentChatId]);
console.log(JSON.stringify({out, painted, reloads}));
""" % _agent_changed_block()
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert data["out"] == [[9, 9], [9, 9], [9, 9], [12, 12], [12, 12]]
    assert data["reloads"] == 9


def test_send_captures_its_target_before_any_await():
    """A voice agent_chat_changed during a send must not redirect the typed message."""
    src = _chat_js()
    send = src.split("async function sendMessage() {", 1)[1]
    after_capture = send.split("const targetChatId = loadedChatId;", 1)
    assert len(after_capture) == 2
    rest = after_capture[1].split("\nasync function ", 1)[0]
    assert "/chats/${targetChatId}/send-to-agent" in rest
    assert "/chats/${loadedChatId}/send-to-agent" not in rest


def test_deleting_the_live_chat_clears_the_star():
    src = _chat_js()
    block = src.split("async function deleteChat(chatId) {", 1)[1].split("\nasync function ", 1)[0]
    assert "if (agentCurrentChatId === chatId) agentCurrentChatId = null;" in block
    assert "loadedChatId = null;" in block


def test_empty_state_never_adopts_an_arbitrary_chat():
    src = _chat_js()
    block = src.split("async function tryAdoptAgentFirstChat(data) {", 1)[1].split(
        "function startEmptyStateAgentWatch()", 1)[0]
    assert "agent_current_chat_id" in block and "chats[0]" not in block


# ------------------------------------------------------------ spoken cap -----

from distr.core.agent.services.tts.sentence_split import SPOKEN_ANNOUNCE_MAX_CHARS as CAP, cap_spoken_text


def test_exactly_at_the_cap_is_unchanged():
    text = ("This sentence is exactly sized. " * 100)[:CAP - 1].rstrip() + "."
    text = text + "x" * (CAP - len(text))
    assert len(text) == CAP
    assert cap_spoken_text(text) == text.strip()


def test_one_over_the_cap_is_capped_within_limit():
    text = "Short one. " + "word " * 400
    out = cap_spoken_text(text)
    assert len(out) <= CAP and out.endswith(".")


def test_no_sentence_breaks_ends_on_a_whole_word_within_cap():
    text = " ".join(f"word{i}" for i in range(600))
    out = cap_spoken_text(text)
    assert len(out) <= CAP and out.endswith(".")
    assert out[:-1].split()[-1] in text.split()


def test_unicode_and_emoji_are_not_split():
    text = "Ça marche très bien 🎉 merci. " * 80
    out = cap_spoken_text(text)
    assert len(out) <= CAP
    out.encode("utf-8")  # no lone surrogates
    assert out.endswith(".") and "🎉" in out


def test_very_long_single_word_stays_within_cap_with_one_full_stop():
    word = "a" * 5000
    out = cap_spoken_text(word)
    assert len(out) <= CAP and out.endswith(".") and not out.endswith("..")


def test_cap_never_doubles_the_closing_punctuation():
    text = ("x" * (CAP - 10)) + "! " + "y" * 3000
    out = cap_spoken_text(text)
    assert not out.endswith("..") and not out.endswith("!.")


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_text_is_empty(text):
    assert cap_spoken_text(text) == ""


# --------------------------------------------------- fast-action turn close --

def _fa_service(tool_run=None, handler=None, opening=1030):
    from distr.core.agent.services.llm.mixins import fast_actions as fa

    closed, stopped = [], []

    class Tool:
        name = "clipboard_action"
        _run = staticmethod(tool_run or (lambda **kw: "CLIPBOARD CONTENT: hi"))

    class Svc(fa.FastActionMixin):
        _tools_dict = {"clipboard_action": Tool()}
        _tools = []
        event_queue = None

        def _normalize_tool_kwargs(self, tool, args):
            return dict(args)

        def _last_user_message_text(self):
            return "read from the clipboard"

        async def _fa_handle_tts_clipboard(self, *a):
            return await handler() if handler else True

        def _fa_close_turn(self, chat_id, turn_id, action, result):
            closed.append((chat_id, turn_id))

        def _fa_close_turn_stopped(self, chat_id, turn_id):
            stopped.append((chat_id, turn_id))

    action = SimpleNamespace(action_type=SimpleNamespace(value="clipboard"), tool_name="clipboard_action",
                             tool_args={"action": "read"}, response_type="tts_clipboard",
                             original_text="read from the clipboard")
    return Svc(), action, closed, stopped


@pytest.fixture
def fa_env(monkeypatch):
    from distr.core.agent.services.llm.mixins import fast_actions as fa
    from distr.core.agent import tool_audit

    monkeypatch.setattr(fa, "_open_turn_id", lambda chat_id: 1030)
    monkeypatch.setattr(fa, "tool_execution_block_reason", lambda *a, **k: "")
    monkeypatch.setattr(fa, "remember_successful_tool_call", lambda *a, **k: None)
    monkeypatch.setattr(tool_audit, "record_tool_execution", lambda *a, **k: None)
    monkeypatch.setattr(tool_audit, "record_tool_start", lambda *a, **k: None)
    return fa


def test_tool_exception_falls_back_to_the_model_and_leaves_turn_for_it(fa_env):
    def boom(**kw):
        raise RuntimeError("clipboard locked")

    svc, action, closed, stopped = _fa_service(tool_run=boom)
    assert run_isolated(svc._execute_fast_action(action, 7)) is False
    assert closed == [] and stopped == []  # the model's reply closes the turn instead


def test_handler_exception_falls_back_too(fa_env):
    async def boom():
        raise RuntimeError("tts gone")

    svc, action, closed, stopped = _fa_service(handler=boom)
    assert run_isolated(svc._execute_fast_action(action, 7)) is False
    assert closed == []


def test_cancelled_read_aloud_closes_its_turn_and_re_raises(fa_env):
    async def cancelled():
        raise asyncio.CancelledError()

    svc, action, closed, stopped = _fa_service(handler=cancelled)
    with pytest.raises(asyncio.CancelledError):
        run_isolated(svc._execute_fast_action(action, 7))
    assert stopped == [(7, 1030)] and closed == []


def test_unhandled_read_does_not_close_the_turn(fa_env):
    async def not_handled():
        return False

    svc, action, closed, stopped = _fa_service(handler=not_handled)
    assert run_isolated(svc._execute_fast_action(action, 7)) is False
    assert closed == []


def test_close_turn_without_ids_is_a_noop(monkeypatch):
    from distr.core.agent.services.llm.mixins import fast_actions as fa
    from distr.core import chat_turns

    calls = []
    monkeypatch.setattr(chat_turns, "complete_turn", lambda *a, **k: calls.append((a, k)))
    for chat_id, turn_id in ((None, 1), (7, None), (0, 0)):
        fa.FastActionMixin._fa_close_turn(SimpleNamespace(), chat_id, turn_id, SimpleNamespace(response_type="tts"), "x")
        fa.FastActionMixin._fa_close_turn_stopped(SimpleNamespace(), chat_id, turn_id)
    assert calls == []


def test_close_turn_error_text_is_shown_and_db_failure_is_swallowed(monkeypatch):
    from distr.core.agent.services.llm.mixins import fast_actions as fa
    from distr.core import chat_turns

    calls = []
    monkeypatch.setattr(chat_turns, "complete_turn", lambda cid, **k: calls.append(k["display_text"]))
    fa.FastActionMixin._fa_close_turn(SimpleNamespace(), 7, 9, SimpleNamespace(response_type="tts"), "Error: no clipboard")
    assert calls == ["Error: no clipboard"]

    def broken(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr(chat_turns, "complete_turn", broken)
    fa.FastActionMixin._fa_close_turn(SimpleNamespace(), 7, 9, SimpleNamespace(response_type="tts"), "ok")
    fa.FastActionMixin._fa_close_turn_stopped(SimpleNamespace(), 7, 9)


# ------------------------------------------------------------ memory capture --

from distr.core.agent import memory_capture as mc


@pytest.mark.parametrize("text", [
    "Remember my passwd is tiger123",
    "From now on use the pwd letmein for staging",
    "I always want the API-key sk-live-abcdefgh1234 in the header",
    "Remember the ssh key lives in my notes",
    "I always use bearer abc.def.ghi for that service",
    "Remember my PIN is 4321",
    "Remember my ID number is 9001010000000",
    "I always want you to call me on 082 555 1234",
    "Remember my number is +27 82 555 1234",
    "Remember my email is paul@example.com",
    "I always use ghp_abcdefghijklmnopqrstuvwxyz for GitHub",
    "Remember the credentials are in the vault",
    "Remember my card number ends in the usual",
    "We decided the private key goes on the server",
])
def test_secret_variants_are_never_captured(text):
    assert mc.extract_memories(text) == []


@pytest.mark.parametrize("text", [
    "Do I always want estimates in agent minutes?",
    "Should we decided to go with Kilo?",
    "From now on, can you reply in English?",
])
def test_questions_are_not_captured(text):
    assert mc.extract_memories(text) == []


def test_duplicates_in_one_message_and_across_messages_are_saved_once(tmp_path):
    msg = "I always want estimates in agent minutes. I always want estimates in agent minutes."
    assert mc.capture(msg, root=tmp_path) == ["I always want estimates in agent minutes."]
    assert mc.capture("Remember: I ALWAYS want estimates in agent minutes!", root=tmp_path) == []
    assert len(mc.remembered_entries(root=tmp_path)) == 1


def test_very_long_text_is_ignored_and_long_sentences_are_trimmed(tmp_path):
    assert mc.extract_memories("I always want short answers. " + "filler " * 1000) == []
    long_pref = "I always want " + "very " * 100 + "short answers."
    found = mc.extract_memories(long_pref)
    assert found and len(found[0][1]) <= mc.MAX_ENTRY_CHARS


def test_prompt_block_stays_within_budget(tmp_path):
    entries = [f"I always want rule number {i} " + "x" * 200 for i in range(40)]
    block = mc.memory_prompt_block(entries)
    body = block.split("\n", 2)[-1]
    assert len(body) <= mc.MAX_PROMPT_CHARS + 600
    assert "rule number 39" in block  # newest kept


@pytest.mark.parametrize("text", [
    "Ich möchte immer kurze Antworten.",
    "Ek wil altyd kort antwoorde hê.",
    "Je veux toujours des réponses courtes.",
])
def test_non_english_is_not_misfiled(text):
    """Non-English preferences are not captured yet; they must not be misfiled either."""
    assert mc.extract_memories(text) == []


def test_non_english_words_inside_an_english_preference_are_kept():
    found = mc.extract_memories("I always want the greeting to say Molo, unjani before the report.")
    assert found == [("user", "I always want the greeting to say Molo, unjani before the report.")]


def test_empty_and_none_are_safe(tmp_path):
    assert mc.extract_memories("") == [] and mc.extract_memories(None) == []
    assert mc.capture("", root=tmp_path) == []
    assert mc.memory_prompt_block([]) == ""
