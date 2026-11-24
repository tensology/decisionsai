"""mode_control must switch in both directions.

Regression for 2026-11-23: the tool checked "push to talk" before "continuous",
so any phrasing mentioning both (\"swap over from continuous mode to push to
talk\", \"switch back from push to talk to continuous\") resolved to PTT. It also
errored (\"Please specify action\") when the model omitted action/text.
"""

import pytest

from distr.core.agent.tools.input.navigation import ModeControlTool


class _Queue:
    def __init__(self):
        self.events = []

    def put(self, item, block=False):
        event, data = item
        self.events.append((event, data.get("enabled")))


@pytest.mark.parametrize(
    "text,expected_enabled",
    [
        ("Can you swap over from continuous mode to push to talk?", False),
        ("can you switch back from continuous mode to push to talk", False),
        ("go to push to talk", False),
        ("enable PTT", False),
        ("Can you switch back from push to talk to continuous?", True),
        ("switch back to continuous mode please", True),
        ("switch to continuous", True),
        ("go hands free", True),
    ],
)
def test_mode_switch_resolves_the_target_direction(text, expected_enabled):
    queue = _Queue()
    tool = ModeControlTool(event_queue=queue)

    # The model often forwards only the user's message.
    tool._run(last_user_message=text)

    assert queue.events, "expected a hands_free_mode_changed event"
    event, enabled = queue.events[-1]
    assert event == "hands_free_mode_changed"
    assert enabled is expected_enabled


def test_explicit_action_still_wins():
    queue = _Queue()
    tool = ModeControlTool(event_queue=queue)
    tool._run(action="ptt", text="switch to continuous")
    assert queue.events[-1][1] is False
