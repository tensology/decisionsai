"""A slow work-request handler must not delay a time-entry push that arrives after it."""
import asyncio
import json
from contextlib import asynccontextmanager

import distr.core.tensology_stream as stream


class _Socket:
    def __init__(self, frames):
        self.frames = frames

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for frame in self.frames:
            yield frame
            await asyncio.sleep(0)

    async def send(self, _data):
        pass


def test_time_entry_event_is_not_queued_behind_work_request(monkeypatch):
    order = []
    time_entry_seen = asyncio.Event()

    def on_time_entry(event):
        if event.get("entry_id"):
            order.append(("time_entry", event["entry_id"]))
            time_entry_seen.set()

    monkeypatch.setattr(stream, "handle_time_entry_signal", on_time_entry)

    async def slow_work_request(event):
        # Finishes only once the later time-entry event has been handled (2 s cap).
        try:
            await asyncio.wait_for(time_entry_seen.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass
        order.append(("work_request", event["request_id"]))

    frames = [
        json.dumps({"type": "connected"}),
        json.dumps({"type": "decisions_work_request", "request_id": "r1", "event": "reconcile_requested"}),
        json.dumps({"type": "decisions_time_entry", "entry_id": 900, "event": "deleted"}),
    ]

    @asynccontextmanager
    async def connect(_url, _key):
        yield _Socket(frames)

    async def noop(*_args, **_kwargs):
        pass

    asyncio.run(stream.run_tensology_ticket_stream(
        connect=connect, catch_up=noop, handle_events=noop, handle_work_request=slow_work_request,
        config=lambda: ("wss://t/ws/decisions", "k"), paused=lambda: True, sleep=noop, max_attempts=1,
    ))
    assert order == [("time_entry", 900), ("work_request", "r1")]
