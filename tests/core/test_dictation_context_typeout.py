from distr.core.agent.services.llm.mixins.voice import VoiceDictationMixin


class DummyDictation(VoiceDictationMixin):
    def __init__(self, messages=None, chat_manager=None):
        self._messages = messages or []
        self.chat_manager = chat_manager


class DummyChatManager:
    def __init__(self, messages):
        self._messages = messages

    def get_current_chat(self):
        return 123

    def get_chat_history(self, chat_id):
        assert chat_id == 123
        return self._messages


class DummyEventQueue:
    def __init__(self):
        self.items = []

    def put(self, item, block=False):
        self.items.append(item)


class DummyLifecycleDictation(VoiceDictationMixin):
    def __init__(self):
        self._is_dictating = False
        self._is_hands_free = False
        self._is_listening = True
        self._hands_free_before_dictation = False
        self._one_shot_dictation_armed = False
        self._dictation_release_pending = False
        self._dictation_one_shot = False
        self._dictation_output_mode = "plain"
        self._dictation_ticket_rewrite = False
        self._dictation_ui_stop_sent = False
        self.event_queue = DummyEventQueue()

    def set_hands_free(self, enabled):
        self._is_hands_free = enabled


def test_contextual_typeout_command_types_last_assistant_message():
    dummy = DummyDictation(messages=[
        {"role": "user", "content": "summarize this"},
        {"role": "assistant", "content": "Here is the concise version."},
    ])

    assert dummy._process_dictation_text("Can you actually type that out?") == (
        "Here is the concise version."
    )


def test_contextual_typeout_command_skips_generic_assistant_messages():
    dummy = DummyDictation(messages=[
        {"role": "assistant", "content": "Use the export button first."},
        {"role": "assistant", "content": "Done"},
    ])

    assert dummy._process_dictation_text("please type that out") == (
        "Use the export button first."
    )


def test_contextual_typeout_command_falls_back_to_chat_history():
    chat_manager = DummyChatManager([
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "Fallback text from the current chat."},
    ])
    dummy = DummyDictation(chat_manager=chat_manager)

    assert dummy._process_dictation_text("write it out please") == (
        "Fallback text from the current chat."
    )


def test_contextual_typeout_command_does_not_type_literal_command_without_context():
    dummy = DummyDictation()

    assert dummy._process_dictation_text("Can you please type that out?") == ""


def test_normal_dictation_text_is_unchanged():
    dummy = DummyDictation(messages=[
        {"role": "assistant", "content": "Previous assistant text."},
    ])

    assert dummy._process_dictation_text("Can you type that out in Python tomorrow?") == (
        "Can you type that out in Python tomorrow?"
    )


def test_hold_dictation_release_posts_single_ui_stop():
    dummy = DummyLifecycleDictation()

    dummy._start_dictation()
    dummy._finish_dictation_after_pending_transcript()
    dummy._stop_dictation()

    assert dummy.event_queue.items.count(("set_dictating", {"enabled": False})) == 1
    assert dummy.event_queue.items.count(("dictation_stopped", {})) == 1


def test_ticket_rewrite_only_runs_for_one_shot_when_enabled(monkeypatch):
    dummy = DummyDictation()
    dummy._dictation_one_shot = True
    dummy._dictation_output_mode = "ticket"

    monkeypatch.setattr(
        dummy,
        "_load_dictation_settings",
        lambda: {
            "dictation_ticket_use_llm": False,
        },
    )

    result = dummy._process_dictation_text(
        "i need the shortcut preferences to let me hold control shift and make a clean ticket"
    )

    assert result.startswith("Title:")
    assert "Summary:" in result
    assert "Acceptance Criteria:" in result


def test_ticket_rewrite_does_not_run_for_persistent_dictation(monkeypatch):
    dummy = DummyDictation()
    dummy._dictation_one_shot = False
    dummy._dictation_output_mode = "ticket"

    monkeypatch.setattr(
        dummy,
        "_load_dictation_settings",
        lambda: {"dictation_ticket_use_llm": False},
    )

    text = "make this a ticket"
    assert dummy._process_dictation_text(text) == text


def test_ticket_rewrite_does_not_run_for_plain_one_shot(monkeypatch):
    dummy = DummyDictation()
    dummy._dictation_one_shot = True
    dummy._dictation_output_mode = "plain"

    monkeypatch.setattr(
        dummy,
        "_load_dictation_settings",
        lambda: {"dictation_ticket_use_llm": False},
    )

    text = "make this a ticket"
    assert dummy._process_dictation_text(text) == text


def test_ticket_formatter_adds_section_breaks():
    from distr.core.audio.ticket_dictation import normalize_ticket_format

    result = normalize_ticket_format(
        "Title: Fix shortcut Summary: Make ticket dictation purple Acceptance Criteria:\n- Works"
    )

    assert result == (
        "Title: Fix shortcut\n\n"
        "Summary: Make ticket dictation purple\n\n"
        "Acceptance Criteria:\n\n"
        "- Works"
    )
