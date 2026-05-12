from distr.core.workflow.planning import _normalize_plan_steps


def test_normalize_plan_steps_replaces_generic_step_titles():
    steps = [
        {
            "title": "Instruction from user",
            "instruction": "Open the DecisionsAI logs and identify why Telegram delivery is flaky.",
            "action_type": "unknown",
        },
        {
            "title": "Step 2",
            "instruction": "Add a regression test for the stuck workflow validation loop.",
            "action_type": "run_command",
        },
    ]

    normalized = _normalize_plan_steps(steps, "Fix the workflow validation and Telegram issues")

    assert normalized[0]["title"] == "Open the DecisionsAI logs and identify why Telegram"
    assert normalized[0]["action_type"] == "agent_instruction"
    assert normalized[0]["validation_type"] == "llm_judgment"
    assert "Open the DecisionsAI logs" in normalized[0]["verification"]
    assert normalized[0]["max_retries"] == 1
    assert normalized[1]["title"] == "Add a regression test for the stuck workflow"
    assert normalized[1]["action_type"] == "run_command"
    assert normalized[1]["config"]["command"] == "Add a regression test for the stuck workflow validation loop."
    assert normalized[1]["timeout_seconds"] == 120


def test_normalize_plan_steps_fills_missing_instruction_from_source():
    normalized = _normalize_plan_steps(
        [{"title": "Task", "instruction": "", "action_type": "agent_instruction"}],
        "Fix TTS cutouts when switching devices.",
    )

    assert normalized[0]["title"] == "Fix TTS cutouts when switching devices"
    assert normalized[0]["instruction"] == "Fix TTS cutouts when switching devices."


def test_normalize_plan_steps_replaces_generic_instruction_from_user():
    normalized = _normalize_plan_steps(
        [{"title": "Instruction from user", "instruction": "Instruction from user"}],
        "Create a Decisions ticket linked to the active workflow.",
    )

    assert normalized[0]["title"] == "Create a Decisions ticket linked to the active"
    assert normalized[0]["instruction"] == "Create a Decisions ticket linked to the active workflow."
    assert "Create a Decisions ticket" in normalized[0]["verification"]


def test_normalize_plan_steps_adds_computer_use_stuck_contract():
    normalized = _normalize_plan_steps(
        [{
            "title": "Click submit",
            "instruction": "Click the visible Submit button and wait for the confirmation.",
            "action_type": "computer_use",
        }],
        "Submit the form.",
    )

    step = normalized[0]
    assert step["config"]["goal"] == "Click the visible Submit button and wait for the confirmation."
    assert step["config"]["max_iterations"] == 12
    assert step["config"]["stuck_threshold"] == 3
    assert "screen" in step["stuck_behavior"].lower()


def test_normalize_plan_steps_adds_http_config_from_instruction():
    normalized = _normalize_plan_steps(
        [{
            "title": "Call health endpoint",
            "instruction": "GET https://example.com/health and confirm it returns 200.",
            "action_type": "http_request",
        }],
        "Check the health endpoint.",
    )

    assert normalized[0]["config"]["url"] == "https://example.com/health"
    assert normalized[0]["config"]["method"] == "GET"
    assert normalized[0]["timeout_seconds"] == 60
