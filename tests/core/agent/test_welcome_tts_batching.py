import asyncio


def test_welcome_message_pushes_single_text_frame_for_tts_batching(monkeypatch):
    from distr.core.agent import libs
    from distr.core.agent.services.llm.core_mixin import LLMSharedMixin

    class TextFrame:
        def __init__(self, text=""):
            self.text = text

    monkeypatch.setattr(libs, "TextFrame", TextFrame)

    class WelcomeFake(LLMSharedMixin):
        def __init__(self):
            self._cancelled = False
            self._speaker_enabled = True
            self._FrameProcessor__started = True
            self._pipeline_direction = object()
            self._messages = []
            self.pushed = []

        async def _build_welcome_sentences(self, agent_name):
            return ["Hello there.", "I am ready.", "What shall we do next?"]

        async def push_frame(self, frame, direction=None):
            self.pushed.append(frame)

        def _apply_context_window(self):
            pass

    service = WelcomeFake()
    asyncio.run(service.send_welcome_message("Heart"))

    text_frames = [frame for frame in service.pushed if isinstance(frame, TextFrame)]
    assert [frame.text for frame in text_frames] == [
        "Hello there. I am ready. What shall we do next?"
    ]
