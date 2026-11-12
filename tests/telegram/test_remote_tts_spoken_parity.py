"""Remote-app TTS must speak the same spoken-style text the desktop orchestrator speaks.

Payloads below are the exact ``send_to_remote`` texts logged on 2026-11-12
between 09:01 and 09:04 SAST (db/logs/decisions.log). No audio is generated and
nothing is sent: delivery goes to an in-memory manager and a recording TTS stub.
"""

from __future__ import annotations

import time

from distr.core.agent.services.llm.text_utils import clean_text_for_tts
from distr.core.human_engagement import voice_friendly_text
from distr.core.integrations.telegram.remote_tts_delivery import (
    build_synthetic_remote_context,
    deliver_remote_tts,
    remote_spoken_text,
    resolve_remote_delivery_context,
)
from distr.core.notification_routing import record_surface_activity, reset_notification_activity

LOGGED_RELIGHT_ISSUE = (
    "the IDE session ran into an issue on RelightSA. It was working on: Every morning, verify and, "
    "when safely possible, recover the RelightSA Eurolux quote automation on production at root@www "
    "dot tensology dot com..... Result: Today's production check did not run — this turn is "
    "audit-only and SSH to root@www dot tensology dot com is blocked by the harness al."
)
LOGGED_SNUZA = (
    "the IDE session finished Snuza. It was working on: In a local path run the Snuza virtualenv "
    "Django management command a local path manage Python file load latest db. Report whether it "
    "completed.... Result: Fresh 2026-11-11 dump set is on the server. Running now. Command completed "
    "successfully on a fresh dump set, exit code 0 in 89 s, ending RELOAD FROM DE."
)
LOGGED_RELIGHT_SYNC = (
    "the IDE session finished RelightSA. It was working on: In a local path run the RelightSA "
    "production database sync command a local path scripts sync database from production Python "
    "file. Report whether it.... Result: Sync succeeded — exit 0, zero errors ( tmp relightsa sync "
    "20261112 090104.log). Fourth clean scheduled run in a row. Restored into PG 18.6 on."
)


def _as_clipped(logged: str) -> str:
    """The notice as it reaches voice shaping.

    initiative/service.py ``_clean_telegram_line(text, 360)`` cuts at 357 chars and
    appends "..."; voice_friendly_text later turned that "..." into the final "."
    seen in the log. Voice shaping therefore sees "...".
    """
    return logged[:-1] + "..."


RAW_RELIGHT_ISSUE = _as_clipped(LOGGED_RELIGHT_ISSUE)
RAW_SNUZA = _as_clipped(LOGGED_SNUZA)
RAW_RELIGHT_SYNC = _as_clipped(LOGGED_RELIGHT_SYNC)


def _desktop_spoken(text: str) -> str:
    """What _cmd_speak_text_directly speaks on the desktop (command_handler.py)."""
    return voice_friendly_text(clean_text_for_tts(text, spoken_prose=True))


class _Manager:
    def __init__(self):
        self.sent = []
        self._cancelled_remote_audio_requests = set()

    def is_connected(self):
        return True

    def _send_websocket_message(self, message):
        self.sent.append(message)
        return True


def _deliver(text: str, mode: str):
    manager = _Manager()
    spoken_inputs = []

    def generate_tts(value):
        spoken_inputs.append(value)
        return None  # no audio file: nothing is synthesised or streamed

    ok = deliver_remote_tts(
        manager,
        text,
        build_synthetic_remote_context({"mode": mode}),
        generate_tts=generate_tts,
        convert_wav_to_ogg=lambda path: path,
        cleanup_files=lambda *args, **kwargs: None,
        send_text_first=True,
    )
    return ok, manager.sent, spoken_inputs


def test_execution_notices_are_spoken_conversationally():
    # 2026-11-12 agent-conversational: spoken percent first, then the sentence.
    assert _desktop_spoken(RAW_SNUZA) == (
        "One hundred percent done. Snuza is done. Fresh 2026-11-11 dump set is on the server. Running now. "
        "Command completed successfully on a fresh dump set, exit code 0 in 89 s."
    )
    assert _desktop_spoken(RAW_RELIGHT_SYNC) == (
        "One hundred percent done. RelightSA is done. Sync succeeded — exit 0, zero errors. Fourth clean scheduled run in a row."
    )
    assert _desktop_spoken(RAW_RELIGHT_ISSUE) == (
        "RelightSA hit a problem. It stopped before finishing, so I can't give you an honest percentage yet. "
        "Today's production check did not run."
    )


def test_spoken_notice_drops_raw_instruction_and_clipped_fragments():
    for text in (RAW_SNUZA, RAW_RELIGHT_SYNC, RAW_RELIGHT_ISSUE):
        spoken = _desktop_spoken(text)
        assert "It was working on" not in spoken
        assert "a local path" not in spoken
        assert "Result:" not in spoken
        assert ".log" not in spoken
        assert not spoken.endswith(("DE.", " al.", " on."))


def test_named_backend_keeps_its_name():
    text = "Codex finished Snuza. It was working on: run tests. Result: All 12 tests passed."
    assert _desktop_spoken(text) == "One hundred percent done. Codex finished Snuza: run tests. All 12 tests passed."


def test_clean_ticket_title_is_kept_raw_instruction_is_dropped():
    titled = "Cursor finished Spotify. It was working on: Tighten the now-playing bar. Result: Done."
    assert _desktop_spoken(titled) == "One hundred percent done. Cursor finished Spotify: Tighten the now-playing bar. Done."
    assert "load latest db" not in _desktop_spoken(RAW_SNUZA)


def test_complete_result_is_kept_whole():
    text = "the IDE session finished Snuza. Result: Tests passed, exit 0."
    assert _desktop_spoken(text) == "One hundred percent done. Snuza is done. Tests passed, exit 0."


def test_unrelated_voice_text_is_unchanged():
    assert voice_friendly_text("Snuza done") == "Snuza done."
    assert voice_friendly_text("That workflow run finished.") == "All done."


def test_remote_spoken_text_matches_desktop_for_proactive_notices():
    for text in (RAW_SNUZA, RAW_RELIGHT_SYNC, RAW_RELIGHT_ISSUE, LOGGED_SNUZA):
        assert remote_spoken_text(text, proactive=True) == _desktop_spoken(text)
        # The desktop re-shapes text that was already shaped upstream; result is stable.
        assert _desktop_spoken(_desktop_spoken(text)) == _desktop_spoken(text)


def test_proactive_remote_delivery_speaks_and_shows_desktop_text():
    ok, sent, spoken_inputs = _deliver(RAW_SNUZA, "proactive")
    assert ok
    assert spoken_inputs == [_desktop_spoken(RAW_SNUZA)]
    assert "It was working on" not in spoken_inputs[0]
    texts = [m["data"]["text"] for m in sent if m.get("type") == "remote_agent_response"]
    # Screen shows digits ("100% done."), audio says the words; the rest matches.
    expected = _desktop_spoken(RAW_SNUZA).replace("One hundred percent done.", "100% done.", 1)
    assert texts and all(t == expected for t in texts)


def test_command_reply_keeps_display_text_but_cleans_audio():
    reply = "**Done.** I updated `settings.py` for you."
    ok, sent, spoken_inputs = _deliver(reply, "command")
    assert ok
    texts = [m["data"]["text"] for m in sent if m.get("type") == "remote_agent_response"]
    assert texts and all(t == reply for t in texts)
    assert spoken_inputs == [" ".join(clean_text_for_tts(reply, spoken_prose=True).split())]
    assert "**" not in spoken_inputs[0] and "`" not in spoken_inputs[0]


def test_remote_priority_routing_is_unchanged():
    reset_notification_activity()
    manager = _Manager()
    manager._pending_remote_agent_responses = []
    manager._pending_remote_agent_response = None
    record_surface_activity("remote", at=time.time())
    ctx = resolve_remote_delivery_context(
        manager,
        {"mode": "proactive", "engagement_source": "initiative", "explicit_notification_intent": True},
    )
    assert ctx is not None and ctx.get("synthetic") is True and ctx.get("mode") == "proactive"
    reset_notification_activity()
