"""Unit tests for distr.core.step_runner.context_assembly."""

import json
from types import SimpleNamespace

import pytest

from distr.core.step_runner.context_assembly import (
    StepInputContext,
    WorkflowInput,
    _load_workflow_input,
    assemble_step_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(context_rules=None, workflow_input=None):
    """Build a mock session with the required attributes."""
    return SimpleNamespace(
        context_rules=context_rules,
        workflow_input=(
            json.dumps(workflow_input) if isinstance(workflow_input, dict) else workflow_input
        ),
    )


def _make_step(step_type="run_command", config=None):
    """Build a mock step with the required attributes."""
    return SimpleNamespace(
        step_type=step_type,
        config=json.dumps(config) if isinstance(config, dict) else config,
    )


SAMPLE_WORKFLOW_INPUT = {
    "source_type": "kanban_ticket",
    "title": "Fix login CSS",
    "text": "The login button is misaligned.",
    "images": ["/img/screenshot.png"],
    "attachments": [{"filename": "log.txt", "path": "/tmp/log.txt"}],
    "metadata": {"ticket_id": "PROJ-42"},
}

SAMPLE_PRIOR_RESULTS = [
    {"result": '{"token": "abc123"}', "title": "Step 1", "step_type": "http_request"},
    {"result": "All tests passed", "title": "Step 2", "step_type": "run_command"},
]


# ---------------------------------------------------------------------------
# WorkflowInput dataclass
# ---------------------------------------------------------------------------

class TestWorkflowInput:
    def test_defaults(self):
        wi = WorkflowInput(source_type="instruction")
        assert wi.source_type == "instruction"
        assert wi.text == ""
        assert wi.title == ""
        assert wi.images == []
        assert wi.attachments == []
        assert wi.metadata == {}

    def test_full_construction(self):
        wi = WorkflowInput(
            source_type="kanban_ticket",
            text="desc",
            title="title",
            images=["/a.png"],
            attachments=[{"filename": "f"}],
            metadata={"k": "v"},
        )
        assert wi.source_type == "kanban_ticket"
        assert wi.images == ["/a.png"]


# ---------------------------------------------------------------------------
# _load_workflow_input
# ---------------------------------------------------------------------------

class TestLoadWorkflowInput:
    def test_none_when_empty(self):
        session = _make_session(workflow_input=None)
        assert _load_workflow_input(session) is None

    def test_none_when_empty_string(self):
        session = _make_session(workflow_input="")
        assert _load_workflow_input(session) is None

    def test_parses_valid_json(self):
        session = _make_session(workflow_input=SAMPLE_WORKFLOW_INPUT)
        wi = _load_workflow_input(session)
        assert wi is not None
        assert wi.source_type == "kanban_ticket"
        assert wi.title == "Fix login CSS"
        assert wi.images == ["/img/screenshot.png"]

    def test_returns_none_on_bad_json(self):
        session = _make_session(workflow_input="{bad json")
        assert _load_workflow_input(session) is None


# ---------------------------------------------------------------------------
# assemble_step_context — agent_instruction
# ---------------------------------------------------------------------------

class TestAssembleAgentInstruction:
    def test_includes_everything(self):
        session = _make_session(
            context_rules="Be concise.",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("agent_instruction", {"instruction": "Do the thing"})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)

        assert ctx.workflow_input is not None
        assert ctx.workflow_input.source_type == "kanban_ticket"
        assert ctx.workflow_input.images == ["/img/screenshot.png"]
        assert ctx.workflow_rules == "Be concise."
        assert len(ctx.previous_results) == 2
        assert ctx.step_config == {"instruction": "Do the thing"}
        # No resolved variables for agent_instruction
        assert ctx.resolved_variables == {}


# ---------------------------------------------------------------------------
# assemble_step_context — run_command
# ---------------------------------------------------------------------------

class TestAssembleRunCommand:
    def test_variables_only(self):
        session = _make_session(
            context_rules="Some rules",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("run_command", {"command": "echo {{step_1.token}}"})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)

        assert ctx.workflow_input is None
        assert ctx.workflow_rules == ""
        assert ctx.previous_results == []
        assert ctx.step_config == {"command": "echo {{step_1.token}}"}
        assert "step_1.token" in ctx.resolved_variables
        assert ctx.resolved_variables["step_1.token"] == "abc123"


# ---------------------------------------------------------------------------
# assemble_step_context — play_recording
# ---------------------------------------------------------------------------

class TestAssemblePlayRecording:
    def test_config_only(self):
        session = _make_session(
            context_rules="Rules",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("play_recording", {"recording_id": 42})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)

        assert ctx.workflow_input is None
        assert ctx.workflow_rules == ""
        assert ctx.previous_results == []
        assert ctx.resolved_variables == {}
        assert ctx.step_config == {"recording_id": 42}


# ---------------------------------------------------------------------------
# assemble_step_context — http_request
# ---------------------------------------------------------------------------

class TestAssembleHttpRequest:
    def test_variables_only(self):
        session = _make_session(
            context_rules="Rules",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("http_request", {"url": "https://api.example.com/{{step_1.token}}"})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)

        assert ctx.workflow_input is None
        assert ctx.workflow_rules == ""
        assert ctx.previous_results == []
        assert "step_1" in ctx.resolved_variables
        assert ctx.step_config["url"] == "https://api.example.com/{{step_1.token}}"


# ---------------------------------------------------------------------------
# assemble_step_context — execute_code
# ---------------------------------------------------------------------------

class TestAssembleExecuteCode:
    def test_text_only_no_images(self):
        session = _make_session(
            context_rules="Use Python 3.12",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("execute_code", {"instruction": "Parse CSV", "code": ""})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)

        assert ctx.workflow_input is not None
        assert ctx.workflow_input.text == "The login button is misaligned."
        # Images stripped for code steps
        assert ctx.workflow_input.images == []
        assert ctx.workflow_input.attachments == []
        assert ctx.workflow_rules == "Use Python 3.12"
        assert "step_1" in ctx.resolved_variables
        # execute_code does NOT get previous_results list
        assert ctx.previous_results == []


# ---------------------------------------------------------------------------
# assemble_step_context — playwright
# ---------------------------------------------------------------------------

class TestAssemblePlaywright:
    def test_text_plus_screenshots(self):
        session = _make_session(
            context_rules="Test on Chrome",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("playwright", {"instruction": "Click login", "headless": True})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)

        assert ctx.workflow_input is not None
        assert ctx.workflow_input.images == []  # text-only
        assert ctx.workflow_rules == "Test on Chrome"
        assert "step_1" in ctx.resolved_variables
        # Playwright gets previous_results for screenshots
        assert len(ctx.previous_results) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_config_on_step(self):
        session = _make_session()
        step = _make_step("run_command", config=None)
        ctx = assemble_step_context(session, step, [])
        assert ctx.step_config == {}

    def test_empty_prior_results(self):
        session = _make_session()
        step = _make_step("agent_instruction")
        ctx = assemble_step_context(session, step, [])
        assert ctx.previous_results == []

    def test_unknown_step_type(self):
        session = _make_session(
            context_rules="Rules",
            workflow_input=SAMPLE_WORKFLOW_INPUT,
        )
        step = _make_step("unknown_type", {"foo": "bar"})
        ctx = assemble_step_context(session, step, SAMPLE_PRIOR_RESULTS)
        # Unknown type treated as agent_instruction — gets full context
        assert ctx.step_config == {"foo": "bar"}
        assert ctx.workflow_input is not None
        assert ctx.workflow_rules == "Rules"
        assert len(ctx.previous_results) == 2

    def test_missing_session_attributes(self):
        """Session without context_rules/workflow_input attributes."""
        session = SimpleNamespace()
        step = _make_step("agent_instruction", {"instruction": "hi"})
        ctx = assemble_step_context(session, step, [])
        assert ctx.workflow_input is None
        assert ctx.workflow_rules == ""
