import asyncio
from types import SimpleNamespace

from distr.core.agent.services.llm.mixins.fast_actions import FastActionMixin


class DummyFastActionService(FastActionMixin):
  def __init__(self, *, telegram: bool = False):
    self._is_telegram_request = telegram
    self.event_queue = []
    self.pushed = []

  def _emit_telegram_response(self, full_content: str = "", follow_up_content: str = ""):
    self.event_queue.append(("send_to_telegram", full_content or follow_up_content))

  async def push_frame(self, frame, direction=None):
    self.pushed.append(frame)


def test_read_this_on_telegram_emits_voice_response():
  service = DummyFastActionService(telegram=True)
  fast_action = SimpleNamespace(original_text="Read this.")
  tool = SimpleNamespace(_read_task=None, _last_read_text=None)

  handled = asyncio.run(
    service._fa_handle_tts(
      fast_action,
      chat_id=92,
      result="READ_ACTION:Hello from the clipboard.",
      tool=tool,
    )
  )

  assert handled is True
  assert service.event_queue == [
    ("send_to_telegram", "Hello from the clipboard."),
  ]
  assert service.pushed == []


def test_read_this_on_desktop_uses_tts_pipeline():
  service = DummyFastActionService(telegram=False)
  fast_action = SimpleNamespace(original_text="Read this.")
  tool = SimpleNamespace(_read_task=None, _last_read_text=None)
  calls = []

  async def _push_tts(text):
    calls.append(text)

  service._fa_push_tts = _push_tts

  asyncio.run(
    service._fa_handle_tts(
      fast_action,
      chat_id=92,
      result="READ_ACTION:Hello from the clipboard.",
      tool=tool,
    )
  )

  assert service.event_queue == []
  assert calls == ["Hello from the clipboard."]
