# Feature: workflow-step-runner-unification, Property 11: Export/import round-trip
"""
Property-based test verifying that exporting an AutoWorkflow to JSON and then
importing the result produces a new AutoWorkflow equivalent to the original —
same name, description, workflow_type, context_rules, step count, step fields
(name, instruction, action_type, step_type, verification, position order),
and variable fields (name, default_value, description). The export SHALL
include `format_version: "2.0"`.

**Validates: Requirements 10.1, 10.2, 10.4**
"""

import contextlib
import json
from datetime import datetime
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base, Action
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowVariable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine():
    """Create an in-memory SQLite engine with unified tables."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


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


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Printable text that avoids NUL bytes (SQLite-safe)
_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="\x00",
    ),
    min_size=1,
    max_size=120,
)

_optional_safe_text = st.one_of(st.none(), _safe_text)

_workflow_types = st.sampled_from(["manual", "instruction", "scheduled"])

_action_types = st.sampled_from([
    "agent_instruction", "run_command", "http_request",
    "execute_code", "playwright", "play_recording",
])

_step_types = st.sampled_from([
    "agent_instruction", "run_command", "http_request",
    "execute_code", "playwright", "play_recording",
])

_validation_types = st.sampled_from(["none", "text_match", "llm_judgment", "rule_based"])

_routing_modes = st.sampled_from(["static", "agent_decision"])

_step_strategy = st.fixed_dictionaries({
    "name": _safe_text,
    "description": st.one_of(st.just(""), _safe_text),
    "action_type": _action_types,
    "step_type": _step_types,
    "instruction": st.one_of(st.just(""), _safe_text),
    "verification": _optional_safe_text,
    "config": st.one_of(
        st.none(),
        st.fixed_dictionaries({
            "timeout": st.integers(min_value=1, max_value=600),
        }),
    ),
    "validation_type": _validation_types,
    "validation_prompt": st.one_of(st.just(""), _safe_text),
    "routing_mode": _routing_modes,
    "routing_prompt": st.one_of(st.just(""), _safe_text),
    "wait_before_next": st.integers(min_value=0, max_value=60),
    "max_retries": st.integers(min_value=0, max_value=5),
    "timeout_seconds": st.integers(min_value=1, max_value=600),
    "require_approval": st.booleans(),
    "wait_for_continue": st.booleans(),
    "code": st.one_of(st.just(""), _safe_text),
    "validation_code": st.one_of(st.just(""), _safe_text),
})

_variable_strategy = st.fixed_dictionaries({
    "name": _safe_text,
    "default_value": st.one_of(st.just(""), _safe_text),
    "description": st.one_of(st.just(""), _safe_text),
})

_workflow_strategy = st.fixed_dictionaries({
    "name": _safe_text,
    "description": st.one_of(st.just(""), _safe_text),
    "workflow_type": _workflow_types,
    "context_rules": _optional_safe_text,
    "steps": st.lists(_step_strategy, min_size=0, max_size=5),
    "variables": st.lists(_variable_strategy, min_size=0, max_size=4),
})


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


class TestExportImportRoundTrip:
    """Property 11: Export/import round-trip."""

    @settings(max_examples=100, deadline=None)
    @given(wf_data=_workflow_strategy)
    def test_export_import_roundtrip_preserves_fields(self, wf_data, tmp_path_factory):
        """**Validates: Requirements 10.1, 10.2, 10.4**

        For any valid AutoWorkflow with steps and variables, exporting to JSON
        and then importing the result SHALL produce a new AutoWorkflow that is
        equivalent to the original — same name, description, workflow_type,
        context_rules, step count, step fields, and variable fields. The export
        SHALL include format_version "2.0".
        """
        engine = _make_engine()
        factory = sessionmaker(bind=engine)

        tmp_dir = tmp_path_factory.mktemp("roundtrip")

        # ── 1. Create the original workflow in the DB ──
        with _session_ctx(factory) as db:
            wf = AutoWorkflow(
                name=wf_data["name"],
                description=wf_data["description"] or None,
                workflow_type=wf_data["workflow_type"],
                context_rules=wf_data["context_rules"],
            )
            db.add(wf)
            db.flush()

            for pos, s_data in enumerate(wf_data["steps"]):
                config_val = (
                    json.dumps(s_data["config"])
                    if isinstance(s_data["config"], dict)
                    else None
                )
                step = AutoWorkflowStep(
                    workflow_id=wf.id,
                    position=pos,
                    name=s_data["name"],
                    description=s_data["description"] or None,
                    action_type=s_data["action_type"],
                    step_type=s_data["step_type"],
                    instruction=s_data["instruction"] or None,
                    verification=s_data["verification"],
                    config=config_val,
                    validation_type=s_data["validation_type"],
                    validation_prompt=s_data["validation_prompt"] or None,
                    routing_mode=s_data["routing_mode"],
                    routing_prompt=s_data["routing_prompt"] or None,
                    wait_before_next=s_data["wait_before_next"],
                    max_retries=s_data["max_retries"],
                    timeout_seconds=s_data["timeout_seconds"],
                    require_approval=s_data["require_approval"],
                    wait_for_continue=s_data["wait_for_continue"],
                    code=s_data["code"] or None,
                    validation_code=s_data["validation_code"] or None,
                )
                db.add(step)

            for v_data in wf_data["variables"]:
                db.add(AutoWorkflowVariable(
                    workflow_id=wf.id,
                    name=v_data["name"],
                    default_value=v_data["default_value"],
                    description=v_data["description"],
                ))

            db.flush()
            original_id = wf.id

        # ── 2. Export the workflow ──
        import distr.core.workflow.service as svc

        def patched_get_session():
            return _session_ctx(factory)

        with patch.object(svc, "get_session", patched_get_session), \
             patch("distr.core.paths.RECORDINGS_DIR", str(tmp_dir / "recordings")), \
             patch("distr.core.paths.DB_DIR", str(tmp_dir / "db")):
            exported = svc.export_workflow(original_id)

        assert exported is not None, "Export should succeed for existing workflow"

        # ── 3. Verify format_version ──
        assert exported["format_version"] == "2.0", "Export must include format_version 2.0"

        # ── 4. Import the exported JSON ──
        with patch.object(svc, "get_session", patched_get_session), \
             patch("distr.core.paths.RECORDINGS_DIR", str(tmp_dir / "recordings")), \
             patch("distr.core.paths.DB_DIR", str(tmp_dir / "db")):
            imported_id = svc.import_workflow(exported)

        assert imported_id is not None, "Import should return a workflow ID"
        assert imported_id != original_id, "Imported workflow should have a new ID"

        # ── 5. Read back and verify equivalence ──
        with _session_ctx(factory) as db:
            original = db.query(AutoWorkflow).filter_by(id=original_id).first()
            imported = db.query(AutoWorkflow).filter_by(id=imported_id).first()

            assert imported is not None, "Imported workflow should exist"

            # Workflow-level fields
            assert imported.name == original.name
            assert (imported.description or "") == (original.description or "")
            assert imported.workflow_type == original.workflow_type
            assert (imported.context_rules or "") == (original.context_rules or "")

            # Step count
            orig_steps = sorted(original.steps, key=lambda s: s.position)
            imp_steps = sorted(imported.steps, key=lambda s: s.position)
            assert len(imp_steps) == len(orig_steps), (
                f"Step count mismatch: {len(imp_steps)} != {len(orig_steps)}"
            )

            # Step field equivalence
            for orig_s, imp_s in zip(orig_steps, imp_steps):
                assert imp_s.name == orig_s.name
                assert (imp_s.instruction or "") == (orig_s.instruction or "")
                assert imp_s.action_type == orig_s.action_type
                assert imp_s.step_type == orig_s.step_type
                assert (imp_s.verification or "") == (orig_s.verification or "")
                assert imp_s.position == orig_s.position

            # Variable count and field equivalence
            orig_vars = sorted(original.variables, key=lambda v: v.name)
            imp_vars = sorted(imported.variables, key=lambda v: v.name)
            assert len(imp_vars) == len(orig_vars), (
                f"Variable count mismatch: {len(imp_vars)} != {len(orig_vars)}"
            )

            for orig_v, imp_v in zip(orig_vars, imp_vars):
                assert imp_v.name == orig_v.name
                assert (imp_v.default_value or "") == (orig_v.default_value or "")
                assert (imp_v.description or "") == (orig_v.description or "")
