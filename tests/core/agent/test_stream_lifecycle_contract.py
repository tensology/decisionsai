from queue import Queue

from distr.core.agent.services.llm.core_mixin import LLMSharedMixin


class _ChatManager:
    def get_current_chat(self):
        return 41


class _Task:
    def __init__(self, done=False):
        self._done = done

    def done(self):
        return self._done


class _Harness(LLMSharedMixin):
    def __init__(self):
        self.chat_manager = _ChatManager()
        self.event_queue = Queue()
        self._generation_task = None


def _events(harness, name):
    return [payload for event, payload in list(harness.event_queue.queue) if event == name]


def test_idle_interruption_does_not_emit_response_terminal_event():
    harness = _Harness()

    harness._emit_interruption_cleanup()

    assert _events(harness, "chat_stream_finished") == []


def test_repeated_interruption_emits_one_terminal_event_for_active_generation():
    harness = _Harness()
    harness._generation_task = _Task(done=False)
    harness._mark_chat_stream_started(41)

    harness._emit_interruption_cleanup()
    harness._emit_interruption_cleanup()

    assert _events(harness, "chat_stream_finished") == [
        {"chat_id": 41, "response_text": "", "status": "cancelled"}
    ]

