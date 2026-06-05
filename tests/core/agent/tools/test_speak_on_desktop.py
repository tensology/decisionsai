from __future__ import annotations

import threading


class _Queue:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []

    def put(self, item, block=False):
        self.items.append(item)


def test_telegram_can_explicitly_request_desktop_speech(monkeypatch):
    from distr.core.agent.tools.system.speak_on_desktop import SpeakOnDesktopTool

    monkeypatch.delenv("DECISIONSAI_ALLOW_TELEGRAM_DESKTOP_TTS", raising=False)
    thread = threading.current_thread()
    previous = getattr(thread, "telegram_request", None)
    thread.telegram_request = True
    queue = _Queue()

    try:
        result = SpeakOnDesktopTool(event_queue=queue)._run("hey Paul, you are amazing!")
    finally:
        if previous is None:
            delattr(thread, "telegram_request")
        else:
            thread.telegram_request = previous

    assert result == "Done"
    assert queue.items == [("speak_on_desktop", {"text": "hey Paul, you are amazing!"})]


def test_telegram_desktop_speech_can_be_explicitly_disabled(monkeypatch):
    from distr.core.agent.tools.system.speak_on_desktop import SpeakOnDesktopTool

    monkeypatch.setenv("DECISIONSAI_ALLOW_TELEGRAM_DESKTOP_TTS", "0")
    thread = threading.current_thread()
    previous = getattr(thread, "telegram_request", None)
    thread.telegram_request = True
    queue = _Queue()

    try:
        result = SpeakOnDesktopTool(event_queue=queue)._run("say this")
    finally:
        if previous is None:
            delattr(thread, "telegram_request")
        else:
            thread.telegram_request = previous

    assert result == "Desktop speech is disabled for Telegram requests in local settings."
    assert queue.items == []
