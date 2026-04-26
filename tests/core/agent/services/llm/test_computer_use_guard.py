from distr.core.agent.services.llm.computer_use_guard import (
    build_computer_use_execution_decisions,
)


def _tc(name: str, args: str = "{}") -> dict:
    return {
        "id": f"call_{name}",
        "function": {
            "name": name,
            "arguments": args,
        },
    }


def test_allows_single_actioning_computer_use_call():
    calls = [_tc("mouse_actions", '{"action":"click"}')]
    decisions = build_computer_use_execution_decisions(calls)
    assert decisions == [{"allow": True, "reason": "ok"}]


def test_blocks_second_actioning_computer_use_call_same_round():
    calls = [
        _tc("mouse_actions", '{"action":"click"}'),
        _tc("move_to_element", '{"element_id": 1}'),
    ]
    decisions = build_computer_use_execution_decisions(calls)
    assert decisions[0]["allow"] is True
    assert decisions[1]["allow"] is False


def test_screenshot_locate_only_is_not_actioning():
    calls = [
        _tc("screenshot_analyzer", '{"prompt":"find save button","execute_action":false}'),
        _tc("mouse_actions", '{"action":"click"}'),
    ]
    decisions = build_computer_use_execution_decisions(calls)
    assert decisions[0]["allow"] is True
    assert decisions[1]["allow"] is True
