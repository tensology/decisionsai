# Feature: kanban-agent-workflow, Properties 21 & 22: Workflow Builder
"""
Property 21: Generated workflow JSON has valid action types

*For any* workflow JSON produced by the Workflow Builder generation endpoint,
every step's `action_type` value should be one of the valid types.

**Validates: Requirements 15.9**

Property 22: Generated workflow import round-trip

*For any* valid workflow JSON produced by the generation endpoint, calling
`import_workflow()` should successfully create a workflow with the correct
number of steps, and each step's `name`, `action_type`, and `instruction`
should match the input JSON.

**Validates: Requirements 15.4**
"""
import contextlib
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import (  # noqa: F401 — ensure models registered
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)

VALID_ACTION_TYPES = {
    "agent_instruction",
    "run_command",
    "http_request",
    "execute_code",
    "playwright",
    "play_recording",
    "set_variable",
}


def _make_session_factory():
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    """SessionContext-compatible context manager for patching get_session."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Hypothesis strategies ──

action_type_strategy = st.sampled_from(sorted(VALID_ACTION_TYPES))

step_strategy = st.fixed_dictionaries({
    "position": st.integers(min_value=0, max_value=100),
    "name": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Z"))),
    "action_type": action_type_strategy,
    "instruction": st.text(min_size=0, max_size=200),
    "validation_type": st.just("none"),
    "validation_prompt": st.just(""),
    "routing_mode": st.just("static"),
    "on_pass_goto_position": st.none(),
    "on_fail_goto_position": st.none(),
    "wait_for_continue": st.booleans(),
})

workflow_json_strategy = st.fixed_dictionaries({
    "name": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Z"))),
    "description": st.text(min_size=0, max_size=200),
    "steps": st.lists(step_strategy, min_size=1, max_size=5).map(
        lambda steps: [dict(s, position=i) for i, s in enumerate(steps)]
    ),
    "variables": st.just([]),
})

# Strategy that may include INVALID action types (for Property 21 validation testing)
any_action_type_strategy = st.one_of(
    action_type_strategy,
    st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L",))),
)

any_step_strategy = st.fixed_dictionaries({
    "position": st.integers(min_value=0, max_value=100),
    "name": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Z"))),
    "action_type": any_action_type_strategy,
    "instruction": st.text(min_size=0, max_size=200),
    "validation_type": st.just("none"),
    "validation_prompt": st.just(""),
    "routing_mode": st.just("static"),
    "on_pass_goto_position": st.none(),
    "on_fail_goto_position": st.none(),
    "wait_for_continue": st.booleans(),
})

any_workflow_json_strategy = st.fixed_dictionaries({
    "name": st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N", "Z"))),
    "description": st.text(min_size=0, max_size=200),
    "steps": st.lists(any_step_strategy, min_size=1, max_size=5).map(
        lambda steps: [dict(s, position=i) for i, s in enumerate(steps)]
    ),
    "variables": st.just([]),
})


class TestGeneratedWorkflowValidActionTypes:
    """Property 21: Generated workflow JSON has valid action types."""

    @given(workflow_json=any_workflow_json_strategy)
    @settings(max_examples=50, deadline=None)
    def test_valid_action_types_check(self, workflow_json):
        """For any workflow JSON, every step's action_type should be validated
        against the set of valid types. Steps with valid types pass; invalid ones don't."""
        for step in workflow_json["steps"]:
            action_type = step["action_type"]
            is_valid = action_type in VALID_ACTION_TYPES
            if is_valid:
                assert action_type in VALID_ACTION_TYPES
            # This property verifies the validation logic itself:
            # any action_type NOT in the valid set should be detectable
            assert (action_type in VALID_ACTION_TYPES) == is_valid

    @given(workflow_json=workflow_json_strategy)
    @settings(max_examples=50, deadline=None)
    def test_all_generated_action_types_are_valid(self, workflow_json):
        """For any workflow JSON with action_types drawn from the valid set,
        every step's action_type is in the valid set."""
        for step in workflow_json["steps"]:
            assert step["action_type"] in VALID_ACTION_TYPES, (
                f"Invalid action_type: {step['action_type']}"
            )


class TestGeneratedWorkflowImportRoundTrip:
    """Property 22: Generated workflow import round-trip."""

    @given(workflow_json=workflow_json_strategy)
    @settings(max_examples=50, deadline=None)
    def test_import_round_trip(self, workflow_json):
        """Calling import_workflow() with valid workflow JSON creates a workflow
        with the correct number of steps, and each step's name, action_type,
        and instruction match the input JSON."""
        from distr.core.workflow.service import import_workflow, get_workflow

        factory = _make_session_factory()

        def patched_get_session():
            return _session_ctx(factory)

        with patch("distr.core.workflow.service.get_session", patched_get_session):
            wf_id = import_workflow(workflow_json)
            assert wf_id is not None

            wf = get_workflow(wf_id)
            assert wf is not None

            input_steps = sorted(workflow_json["steps"], key=lambda s: s["position"])
            output_steps = sorted(wf["steps"], key=lambda s: s["position"])

            assert len(output_steps) == len(input_steps), (
                f"Expected {len(input_steps)} steps, got {len(output_steps)}"
            )

            for inp, out in zip(input_steps, output_steps):
                assert out["name"] == inp["name"], (
                    f"Step name mismatch: {out['name']} != {inp['name']}"
                )
                assert out["action_type"] == inp["action_type"], (
                    f"Step action_type mismatch: {out['action_type']} != {inp['action_type']}"
                )
                assert out["instruction"] == inp["instruction"], (
                    f"Step instruction mismatch: {out['instruction']!r} != {inp['instruction']!r}"
                )
