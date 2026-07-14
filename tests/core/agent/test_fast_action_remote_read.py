import asyncio
from types import SimpleNamespace

from distr.core.agent.services.llm.mixins.fast_actions import FastActionMixin


class DummyFastActionService(FastActionMixin):
  def __init__(self, *, telegram: bool = False):
    self._is_telegram_request = telegram
    self._cancelled = False
    self.event_queue = []
    self.pushed = []
    self._messages = []
    self._processed_fast_actions = set()
    self.chat_manager = None

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


def test_developer_context_remote_response_never_uses_desktop_tts(monkeypatch):
  service = DummyFastActionService(telegram=True)
  fast_action = SimpleNamespace(original_text="Can you see what I'm working on in Codex?")
  monkeypatch.setattr(
    "distr.core.external_agent_context.build_agent_visibility_answer",
    lambda _request: "I can see your active Codex task.",
  )

  handled = asyncio.run(
    service._fa_handle_developer_context(fast_action, 92, "unused", tool=None)
  )

  assert handled is True
  assert service.event_queue == [
    ("send_to_telegram", "I can see your active Codex task."),
  ]
  assert service.pushed == []


def test_screenshot_failure_remote_response_never_uses_desktop_tts():
  service = DummyFastActionService(telegram=True)
  fast_action = SimpleNamespace(
    original_text="Click the submit button",
    tool_name="screenshot_analyzer",
  )

  handled = asyncio.run(
    service._fa_handle_screenshot_analyzer(
      fast_action,
      92,
      "TARGET NOT FOUND: Submit button is not visible",
      tool=None,
    )
  )

  assert handled is True
  assert service.event_queue == [
    ("send_to_telegram", "I couldn't find that on screen. Submit button is not visible"),
  ]
  assert service.pushed == []


def test_done_acknowledgement_remote_response_never_uses_desktop_tts():
  from distr.core.agent.services.llm.fast_action_detector import ActionType

  service = DummyFastActionService(telegram=True)
  fast_action = SimpleNamespace(action_type=ActionType.OPEN_WINDOW)
  tool = SimpleNamespace(name="open_window")

  handled = asyncio.run(
    service._fa_handle_done(fast_action, 92, "Opened Safari", tool)
  )

  assert handled is True
  assert len(service.event_queue) == 1
  assert service.event_queue[0][0] == "send_to_telegram"
  assert service.pushed == []


def test_clipboard_rework_ack_uses_fast_action_tool_name_and_routes_remotely():
  service = DummyFastActionService(telegram=True)
  fast_action = SimpleNamespace(
    response_type="done",
    tool_name="rework_clipboard",
  )

  handled = asyncio.run(
    service._fa_handle_clipboard_rework(fast_action, 92, "Rewritten clipboard text")
  )

  assert handled is True
  assert len(service.event_queue) == 1
  assert service.event_queue[0][0] == "send_to_telegram"
  assert service.pushed == []


def test_action_playback_success_acknowledges_remote_without_desktop_tts():
  service = DummyFastActionService(telegram=True)

  handled = asyncio.run(
    service._fa_handle_action_playback(
      SimpleNamespace(),
      92,
      "Playing morning routine",
      tool=None,
    )
  )

  assert handled is True
  assert service.event_queue == [
    ("send_to_telegram", "Playing morning routine"),
  ]
  assert service.pushed == []
