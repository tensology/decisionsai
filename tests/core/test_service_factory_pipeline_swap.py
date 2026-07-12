from distr.core.agent import service_factory


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
