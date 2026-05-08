import distr.core.signals as signals_mod


class _QueueStub:
    def __init__(self):
        self.events = []

    def put(self, item, block=False):
        self.events.append(item)


def test_speak_text_directly_event_queue_deduplicates_burst(monkeypatch):
    q = _QueueStub()
    monkeypatch.setattr(signals_mod, "_agent_event_queue", q)
    monkeypatch.setattr(signals_mod, "_last_speak_text", "")
    monkeypatch.setattr(signals_mod, "_last_speak_ts", 0.0)

    now = {"value": 1000.0}

    def _fake_time():
        return now["value"]

    monkeypatch.setattr(signals_mod.time, "time", _fake_time)

    signals_mod.speak_text_directly_event_queue("Done.")
    signals_mod.speak_text_directly_event_queue("Done.")
    now["value"] += 1.1
    signals_mod.speak_text_directly_event_queue("Done.")

    assert len(q.events) == 2
    assert q.events[0][1]["text"] == "Done."
    assert q.events[1][1]["text"] == "Done."
