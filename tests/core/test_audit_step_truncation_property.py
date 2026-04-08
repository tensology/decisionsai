# Feature: workflow-step-runner-unification, Property 9: Audit step field preservation and truncation
"""
Property-based test verifying that `append_audit_step()` correctly:

- Preserves tool_name as tool_used on the created step
- Truncates instruction to at most 500 characters
- Truncates result to at most 2003 characters (2000 + "...")
- Preserves routing_path in full without any truncation

**Validates: Requirements 7.1, 7.3**
"""

import contextlib
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowRun,
    AutoWorkflowStepResult,
    AutoWorkflowVariable,
)
from distr.core.workflow.service import append_audit_step


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_audit_workflow.py)
# ---------------------------------------------------------------------------

def _make_session_factory():
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _patch_get_session(factory):
    """Return a patcher that replaces get_session in the workflow service."""
    return patch(
        "distr.core.workflow.service.get_session",
        lambda: _session_ctx(factory),
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Tool names: simple identifiers
_tool_name = st.from_regex(r"[a-z][a-z0-9_]{0,29}", fullmatch=True)

# Instructions: varying length 0–2000 chars
_instruction = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=2000,
)

# Results: varying length 0–5000 chars
_result = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=5000,
)

# Routing path: varying length 0–10000 chars
_routing_path = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=0,
    max_size=10000,
)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(
    tool_name=_tool_name,
    instruction=_instruction,
    result=_result,
    routing_path=_routing_path,
)
def test_audit_step_field_preservation_and_truncation(
    tool_name: str,
    instruction: str,
    result: str,
    routing_path: str,
) -> None:
    """**Validates: Requirements 7.1, 7.3**

    For any tool execution data (tool_name, instruction of 0–2000 chars,
    result of 0–5000 chars, routing_path of 0–10000 chars),
    append_audit_step() SHALL create an AutoWorkflowStep where:
    - tool_used equals the tool_name
    - instruction length is at most 500 characters
    - result length is at most 2003 characters (2000 + "...")
    - routing_path equals the full input without truncation
    """
    factory = _make_session_factory()
    chat_id = 999

    with _patch_get_session(factory):
        ok = append_audit_step(
            chat_id=chat_id,
            tool_name=tool_name,
            instruction=instruction,
            result=result,
            status="completed",
            routing_path=routing_path,
        )

    assert ok is True, "append_audit_step should return True"

    # Read back the created step
    session = factory()
    wf = session.query(AutoWorkflow).filter(AutoWorkflow.chat_id == chat_id).first()
    assert wf is not None, "Audit workflow should have been created"
    assert len(wf.steps) == 1, "Exactly one step should have been created"
    step = wf.steps[0]

    # tool_used equals tool_name
    assert step.tool_used == tool_name, (
        f"tool_used {step.tool_used!r} != tool_name {tool_name!r}"
    )

    # instruction length ≤ 500
    assert len(step.instruction) <= 500, (
        f"instruction length {len(step.instruction)} exceeds 500"
    )

    # result length ≤ 2003 (2000 + "...")
    if step.result is not None:
        assert len(step.result) <= 2003, (
            f"result length {len(step.result)} exceeds 2003"
        )

    # routing_path equals the full input (no truncation)
    assert step.routing_path == routing_path, (
        f"routing_path was truncated or altered: "
        f"expected len={len(routing_path)}, got len={len(step.routing_path or '')}"
    )

    session.close()
