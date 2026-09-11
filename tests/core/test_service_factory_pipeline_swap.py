from distr.core.agent import service_factory
from distr.core.agent.session import AgentSession


class Processor:
    def __init__(self, name):
        self.name = name
        self._prev = None
        self._next = None
        self._task_manager = None
        self._clock = None
        self._observer = None


class Pipeline:
    def __init__(self, processors):
        self._processors = processors


class DrainTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def test_swap_processor_recovers_links_from_pipeline_neighbors():
    input_proc = Processor("input")
    llm_proc = Processor("llm")
    old_tts = Processor("old_tts")
    new_tts = Processor("new_tts")
    output_proc = Processor("output")
    pipeline = Pipeline([input_proc, llm_proc, old_tts, output_proc])

    assert service_factory.swap_processor_in_pipeline(pipeline, old_tts, new_tts)

    assert pipeline._processors == [input_proc, llm_proc, new_tts, output_proc]
    assert llm_proc._next is new_tts
    assert new_tts._prev is llm_proc
    assert new_tts._next is output_proc
    assert output_proc._prev is new_tts
    assert old_tts._prev is None
    assert old_tts._next is None


def test_swap_processor_cancels_retired_drain_tasks():
    previous = Processor("previous")
    old = Processor("old")
    replacement = Processor("replacement")
    following = Processor("following")
    input_task = DrainTask()
    process_task = DrainTask()
    old._FrameProcessor__input_frame_task = input_task
    old._FrameProcessor__process_frame_task = process_task
    pipeline = Pipeline([previous, old, following])

    assert service_factory.swap_processor_in_pipeline(pipeline, old, replacement)

    assert input_task.cancelled is True
    assert process_task.cancelled is True
    assert old._FrameProcessor__input_frame_task is None
    assert old._FrameProcessor__process_frame_task is None


def test_elevenlabs_hot_swap_match_includes_model_id():
    service_type = type(
        "ElevenLabsTTSService",
        (),
        {"__module__": "distr.core.agent.services.tts.elevenlabs"},
    )
    service = service_type()
    service.voice_id = "same-clone-id"
    service.model_id = "eleven_flash_v2_5"

    matches = AgentSession._tts_service_matches_target

    assert matches(
        None,
        service,
        target_engine="elevenlabs",
        target_voice_name=None,
        target_voice_id="same-clone-id",
        target_model_id="eleven_flash_v2_5",
    )
    assert not matches(
        None,
        service,
        target_engine="elevenlabs",
        target_voice_name=None,
        target_voice_id="same-clone-id",
        target_model_id="eleven_v3_conversational",
    )
