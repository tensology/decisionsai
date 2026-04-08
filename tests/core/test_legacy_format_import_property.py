# Feature: workflow-step-runner-unification, Property 12: Legacy format import conversion
"""
Property-based test verifying that importing a legacy StepRunner export document
(no `format_version` or `format_version: "1.0"`) produces a valid AutoWorkflow
with fields mapped according to the migration field mapping:
- `instruction` → `description`
- `session_type` → `workflow_type` (instruction→instruction, scheduled→scheduled, unknown→manual)
- `title` → `name` (for steps)
- `context_rules` preserved
- Step fields preserved (instruction, step_type, verification, config, code, position)
- Variable fields preserved (name, default_value, description)

**Validates: Requirements 10.3**
"""

import contextlib
import json
from unittest.mock import patch

from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from distr.core.db import Base
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

_legacy_session_types = st.sampled_from(["instruction", "scheduled"])

_legacy_statuses = st.sampled_from(["planned", "in_progress", "completed"])

_step_types = st.sampled_from([
    "agent_instruction", "run_command", "http_request",
    "execute_code", "playwright", "play_recording",
])

_step_config = st.one_of(
    st.none(),
    st.fixed_dictionaries({
        "timeout": st.integers(min_value=1, max_value=600),
    }),
)

_legacy_step_strategy = st.fixed_dictionaries({
    "title": _safe_text,
    "instruction": _safe_text,
    "step_type": _step_types,
    "position": st.integers(min_value=0, max_value=99),
    "verification": _optional_safe_text,
    "config": _step_config,
    "code": st.one_of(st.none(), _safe_text),
})

_variable_strategy = st.fixed_dictionaries({
    "name": _safe_text,
    "default_value": st.one_of(st.just(""), _safe_text),
    "description": st.one_of(st.just(""), _safe_text),
})


def _unique_name_variables(vars_list):
    """Filter to ensure variable names are unique."""
    seen = set()
    result = []
    for v in vars_list:
        if v["name"] not in seen:
            seen.add(v["name"])
            result.append(v)
    return result

# Legacy format version: either absent (None) or "1.0"
_legacy_format_version = st.sampled_from([None, "1.0"])


def _legacy_doc_strategy():
    """Build a strategy for legacy StepRunner export documents."""
    return st.fixed_dictionaries({
        "format_version": _legacy_format_version,
        "instruction": _safe_text,
        "session_type": _legacy_session_types,
        "status": _legacy_statuses,
        "context_rules": _optional_safe_text,
        "steps": st.lists(_legacy_step_strategy, min_size=0, max_size=5).filter(
            lambda steps: len(steps) == len({s["position"] for s in steps})
        ),
        "variables": st.lists(_variable_strategy, min_size=0, max_size=4).map(
            _unique_name_variables
        ),
    })


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


class TestLegacyFormatImportConversion:
    """Property 12: Legacy format import conversion."""

    @settings(max_examples=100, deadline=None)
    @given(doc=_legacy_doc_strategy())
    def test_legacy_import_maps_fields_correctly(self, doc, tmp_path_factory):
        """**Validates: Requirements 10.3**

        For any valid legacy StepRunner export document (no format_version or
        format_version "1.0"), importing SHALL produce a valid AutoWorkflow with
        fields mapped according to the migration field mapping:
        - instruction → description
        - session_type → workflow_type (instruction→instruction, scheduled→scheduled)
        - title → name (for steps)
        - context_rules preserved
        - Step fields preserved (instruction, step_type, verification, config, code, position)
        - Variable fields preserved (name, default_value, description)
        """
        engine = _make_engine()
        factory = sessionmaker(bind=engine)
        tmp_dir = tmp_path_factory.mktemp("legacy_import")

        # Build the legacy document dict, removing format_version key if None
        legacy_data = dict(doc)
        if legacy_data["format_version"] is None:
            del legacy_data["format_version"]

        import distr.core.workflow.service as svc

        def patched_get_session():
            return _session_ctx(factory)

        with patch.object(svc, "get_session", patched_get_session), \
             patch("distr.core.paths.RECORDINGS_DIR", str(tmp_dir / "recordings")), \
             patch("distr.core.paths.DB_DIR", str(tmp_dir / "db")):
            wf_id = svc.import_workflow(legacy_data)

        assert wf_id is not None, "Import should return a workflow ID"

        with _session_ctx(factory) as db:
            wf = db.query(AutoWorkflow).filter_by(id=wf_id).first()
            assert wf is not None, "Imported workflow should exist"

            # instruction → description
            assert wf.description == doc["instruction"]

            # session_type → workflow_type
            expected_type_map = {
                "instruction": "instruction",
                "scheduled": "scheduled",
            }
            expected_wf_type = expected_type_map.get(doc["session_type"], "manual")
            assert wf.workflow_type == expected_wf_type, (
                f"session_type '{doc['session_type']}' should map to "
                f"workflow_type '{expected_wf_type}', got '{wf.workflow_type}'"
            )

            # context_rules preserved
            expected_ctx = doc["context_rules"] if doc["context_rules"] else None
            actual_ctx = wf.context_rules
            # Normalize: empty string and None are equivalent
            assert (actual_ctx or None) == (expected_ctx or None), (
                f"context_rules mismatch: expected {expected_ctx!r}, got {actual_ctx!r}"
            )

            # Steps verification
            steps = sorted(
                db.query(AutoWorkflowStep).filter_by(workflow_id=wf_id).all(),
                key=lambda s: s.position,
            )
            assert len(steps) == len(doc["steps"]), (
                f"Step count mismatch: {len(steps)} != {len(doc['steps'])}"
            )

            source_steps = sorted(doc["steps"], key=lambda s: s["position"])
            for src, imp_step in zip(source_steps, steps):
                # title → name
                assert imp_step.name == src["title"], (
                    f"Step name mismatch: expected {src['title']!r}, got {imp_step.name!r}"
                )
                # instruction preserved
                assert (imp_step.instruction or "") == (src["instruction"] or "")
                # step_type preserved
                assert imp_step.step_type == src["step_type"]
                # verification preserved
                assert (imp_step.verification or "") == (src["verification"] or "")
                # config preserved
                if src["config"] is not None:
                    assert imp_step.config is not None
                    assert json.loads(imp_step.config) == src["config"]
                # code preserved
                assert (imp_step.code or "") == (src["code"] or "")
                # position preserved
                assert imp_step.position == src["position"]

            # Variables verification
            variables = db.query(AutoWorkflowVariable).filter_by(
                workflow_id=wf_id
            ).all()
            assert len(variables) == len(doc["variables"]), (
                f"Variable count mismatch: {len(variables)} != {len(doc['variables'])}"
            )

            imp_vars_by_name = {v.name: v for v in variables}
            for src_var in doc["variables"]:
                assert src_var["name"] in imp_vars_by_name, (
                    f"Variable '{src_var['name']}' not found in imported workflow"
                )
                imp_var = imp_vars_by_name[src_var["name"]]
                assert (imp_var.default_value or "") == (src_var["default_value"] or "")
                assert (imp_var.description or "") == (src_var["description"] or "")
