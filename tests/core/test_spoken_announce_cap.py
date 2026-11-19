"""Background-chain replies must be spoken in full, never cut mid-sentence.

Regression: a 668-character reply was sliced with ``[:500]`` before TTS, so the
voice stopped part way through "a Merrypak Django e..." and the last sentence
was never spoken.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import distr.core.signals as signals
from distr.core.agent.services.llm.background_chain import BackgroundChainRunner
from distr.core.agent.services.tts.sentence_split import cap_spoken_text

REPLY_668 = (
    "I'm good, thanks \u2014 busy watching the place for you, which is kind of the point. "
    "Infrastructure-wise it's mostly healthy but a bit untidy: My Board has eight tickets "
    "with no project linked and no default project set, so anything dispatched from there "
    "could fail, and a few boards \u2014 Merrypak, Spoortrack, Jackcat \u2014 have backlogs "
    "piling up while their Current lanes sit empty. There's real work flowing in too: eight "
    "high-priority items landed, three from Gmail, two from Jira, a Merrypak Django error on "
    "the category products endpoint, and that recurring Celery worker-dead alert on the "
    "jackcat host. Nothing on fire, but the My Board scoping is the thing I'd fix first."
)


def _announce(content: str, monkeypatch) -> list[str]:
    spoken: list[str] = []
    monkeypatch.setattr(signals, "speak_text_directly_event_queue", lambda t: spoken.append(t) or True)
    service = SimpleNamespace(
        _speaker_enabled=True,
        _is_telegram_request=False,
        chat_manager=None,
        _messages=[],
    )
    runner = BackgroundChainRunner(service, [], [], chat_id=None)
    asyncio.run(runner._announce_result(content))
    return spoken


def test_700_char_reply_is_spoken_in_full(monkeypatch):
    assert 600 < len(REPLY_668) < 720
    spoken = _announce(REPLY_668, monkeypatch)
    assert len(spoken) == 1
    assert spoken[0].endswith("the thing I'd fix first.")
    assert "Celery worker-dead alert on the jackcat host." in spoken[0]
    assert len(spoken[0]) >= len(REPLY_668) - 5


def test_long_report_is_capped_on_a_sentence_boundary():
    sentence = "Board twelve has a ticket waiting for review in the Current lane. "
    text = (sentence * 60).strip()
    capped = cap_spoken_text(text, max_chars=500)
    assert len(capped) <= 500
    assert capped.endswith("Current lane.")


def test_single_oversized_sentence_ends_on_a_whole_word():
    text = "word " * 400
    capped = cap_spoken_text(text, max_chars=120)
    assert len(capped) <= 121
    assert capped.endswith("word.")


def test_short_text_is_unchanged():
    assert cap_spoken_text("  All done.  ") == "All done."
