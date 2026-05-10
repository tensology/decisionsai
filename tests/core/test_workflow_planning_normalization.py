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
    assert normalized[1]["title"] == "Add a regression test for the stuck workflow"
    assert normalized[1]["action_type"] == "run_command"


def test_normalize_plan_steps_fills_missing_instruction_from_source():
    normalized = _normalize_plan_steps(
        [{"title": "Task", "instruction": "", "action_type": "agent_instruction"}],
        "Fix TTS cutouts when switching devices.",
    )

    assert normalized[0]["title"] == "Fix TTS cutouts when switching devices"
    assert normalized[0]["instruction"] == "Fix TTS cutouts when switching devices."
