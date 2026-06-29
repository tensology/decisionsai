import asyncio
from types import SimpleNamespace

from distr.core.agent.services.llm.mixins.fast_actions import FastActionMixin
from distr.core.agent.services.llm.text_utils import clean_text_for_tts


def test_tts_symbol_pronunciation_makes_clipboard_markup_readable():
    raw = "[Paul] | @design & ops {urgent}"

    spoken = clean_text_for_tts(raw, spoken_prose=True, speakable_symbols=True)

    assert spoken == "Paul. at design and ops. urgent."
    assert "[" not in spoken
    assert "]" not in spoken
    assert "|" not in spoken
    assert "@" not in spoken
    assert "&" not in spoken
    assert "{" not in spoken
    assert "}" not in spoken


def test_tts_symbol_pronunciation_handles_arrow_like_symbols_before_equals():
    raw = "status => ready & draft -> review"

    spoken = clean_text_for_tts(raw, spoken_prose=True, speakable_symbols=True)

    assert spoken == "status becomes ready and draft to review"
    assert "=>" not in spoken
    assert "->" not in spoken


def test_clipboard_tts_speaks_cleaned_text_but_records_exact_clipboard_text():
    class _Harness(FastActionMixin):
        def __init__(self):
            self.spoken = []
            self.read_records = []
            self._is_telegram_request = False

        async def _fa_push_tts(self, text):
            self.spoken.append(text)

        def _fa_record_read_aloud_activity(self, chat_id, label, text, user_text=None):
            self.read_records.append((chat_id, label, text, user_text))

    harness = _Harness()
    raw = "CLIPBOARD CONTENT:\n\n[Paul] | @design & ops {urgent}"
    fast_action = SimpleNamespace(original_text="read from clipboard")

    asyncio.run(harness._fa_handle_tts_clipboard(fast_action, 707, raw, tool=None))

    assert harness.spoken == ["Paul. at design and ops. urgent."]
    assert harness.read_records == [
        (707, "Read from clipboard", "[Paul] | @design & ops {urgent}", "read from clipboard")
    ]
