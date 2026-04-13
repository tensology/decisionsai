# Feature: workflow-step-runner-unification, Task 12.2
# Tests for import_workflow() with unified and legacy format handling
# Validates: Requirements 10.2, 10.3
"""
Unit tests for import_workflow():
- Unified format (format_version '2.0') imports with all new fields
- Legacy format (no format_version or '1.0') converts using migration field mapping
- Legacy step title → name conversion
- Legacy session_type → workflow_type mapping
- Legacy instruction → description mapping
"""
import contextlib
import json
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base
from distr.core.db.workflow import (
    AutoWorkflow,
    AutoWorkflowStep,
    AutoWorkflowVariable,
)


def _make_session_factory():
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


class TestImportWorkflowUnifiedFormat:
    """Tests for importing unified format (format_version '2.0') documents."""

    def test_imports_workflow_type(self):
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        data = {
            "format_version": "2.0",
            "name": "Test WF",
            "description": "A test",
            "workflow_type": "scheduled",
            "context_rules": "some rules",
            "steps": [],
            "variables": [],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)
            assert wf_id is not None

            with _session_ctx(factory) as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
                assert wf.workflow_type == "scheduled"
                assert wf.context_rules == "some rules"
                assert wf.description == "A test"

    def test_imports_step_type_and_verification(self):
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        data = {
            "format_version": "2.0",
            "name": "WF",
            "steps": [
                {
                    "position": 0,
                    "name": "Step 1",
                    "instruction": "do something",
                    "step_type": "execute_code",
                    "verification": "check output",
                    "config": {"lang": "python"},
                }
            ],
            "variables": [],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)

            with _session_ctx(factory) as db:
                steps = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.workflow_id == wf_id
                ).all()
                assert len(steps) == 1
                assert steps[0].step_type == "execute_code"
                assert steps[0].verification == "check output"
                assert json.loads(steps[0].config) == {"lang": "python"}

    def test_invalid_workflow_type_defaults_to_manual(self):
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        data = {
            "format_version": "2.0",
            "name": "WF",
            "workflow_type": "bogus_type",
            "steps": [],
            "variables": [],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)

            with _session_ctx(factory) as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
                assert wf.workflow_type == "manual"


class TestImportWorkflowLegacyFormat:
    """Tests for importing legacy StepRunner session format (no format_version or '1.0')."""

    def test_legacy_no_format_version(self):
        """Legacy doc without format_version is converted correctly."""
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        data = {
            "instruction": "Check my email every morning",
            "session_type": "scheduled",
            "status": "planned",
            "context_rules": "Use Gmail",
            "steps": [
                {
                    "title": "Open Gmail",
                    "instruction": "Navigate to Gmail",
                    "step_type": "playwright",
                    "position": 0,
                    "verification": "Page loaded",
                },
                {
                    "title": "Read inbox",
                    "instruction": "Check unread messages",
                    "step_type": "run_command",
                    "position": 1,
                },
            ],
            "variables": [
                {"name": "email", "default_value": "test@example.com", "description": "Email addr"}
            ],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)

            with _session_ctx(factory) as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
                # instruction → description
                assert wf.description == "Check my email every morning"
                # session_type → workflow_type
                assert wf.workflow_type == "scheduled"
                assert wf.context_rules == "Use Gmail"
                assert wf.status == "draft"  # planned → draft

                steps = sorted(
                    db.query(AutoWorkflowStep).filter(
                        AutoWorkflowStep.workflow_id == wf_id
                    ).all(),
                    key=lambda s: s.position,
                )
                assert len(steps) == 2
                # title → name
                assert steps[0].name == "Open Gmail"
                assert steps[0].step_type == "playwright"
                assert steps[0].verification == "Page loaded"
                assert steps[1].name == "Read inbox"
                assert steps[1].step_type == "run_command"

                # Variables preserved
                variables = db.query(AutoWorkflowVariable).filter(
                    AutoWorkflowVariable.workflow_id == wf_id
                ).all()
                assert len(variables) == 1
                assert variables[0].name == "email"

    def test_legacy_format_version_1_0(self):
        """Legacy doc with format_version '1.0' is converted correctly."""
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        data = {
            "format_version": "1.0",
            "instruction": "Deploy the app",
            "session_type": "instruction",
            "status": "in_progress",
            "steps": [
                {
                    "title": "Build",
                    "instruction": "npm run build",
                    "step_type": "run_command",
                    "position": 0,
                }
            ],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)

            with _session_ctx(factory) as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
                assert wf.description == "Deploy the app"
                assert wf.workflow_type == "instruction"
                assert wf.status == "draft"  # status mapped but import always sets draft

                steps = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.workflow_id == wf_id
                ).all()
                assert len(steps) == 1
                assert steps[0].name == "Build"

    def test_legacy_unknown_session_type_defaults_to_manual(self):
        """Unknown session_type maps to 'manual' workflow_type."""
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        data = {
            "instruction": "Do stuff",
            "session_type": "unknown_type",
            "steps": [],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)

            with _session_ctx(factory) as db:
                wf = db.query(AutoWorkflow).filter(AutoWorkflow.id == wf_id).first()
                assert wf.workflow_type == "manual"

    def test_legacy_step_config_preserved(self):
        """Legacy step config (JSON string or dict) is preserved through conversion."""
        from distr.core.workflow.service import import_workflow

        factory = _make_session_factory()
        patched = lambda: _session_ctx(factory)

        config_data = {"url": "https://example.com", "method": "GET"}
        data = {
            "instruction": "API test",
            "steps": [
                {
                    "title": "Call API",
                    "instruction": "Make request",
                    "step_type": "http_request",
                    "config": config_data,
                    "position": 0,
                    "code": "print('hello')",
                    "verification": "Status 200",
                }
            ],
        }

        with patch("distr.core.workflow.import_export.get_session", patched):
            wf_id = import_workflow(data)

            with _session_ctx(factory) as db:
                step = db.query(AutoWorkflowStep).filter(
                    AutoWorkflowStep.workflow_id == wf_id
                ).first()
                assert step.name == "Call API"
                assert step.step_type == "http_request"
                assert json.loads(step.config) == config_data
                assert step.code == "print('hello')"
                assert step.verification == "Status 200"


class TestIsLegacyFormat:
    """Tests for _is_legacy_format helper."""

    def test_no_format_version_is_legacy(self):
        from distr.core.workflow.service import _is_legacy_format
        assert _is_legacy_format({}) is True

    def test_format_version_1_0_is_legacy(self):
        from distr.core.workflow.service import _is_legacy_format
        assert _is_legacy_format({"format_version": "1.0"}) is True

    def test_format_version_2_0_is_not_legacy(self):
        from distr.core.workflow.service import _is_legacy_format
        assert _is_legacy_format({"format_version": "2.0"}) is False

    def test_format_version_other_is_not_legacy(self):
        from distr.core.workflow.service import _is_legacy_format
        assert _is_legacy_format({"format_version": "3.0"}) is False


class TestConvertLegacyToUnified:
    """Tests for _convert_legacy_to_unified helper."""

    def test_session_type_mapping(self):
        from distr.core.workflow.service import _convert_legacy_to_unified

        result = _convert_legacy_to_unified({"session_type": "instruction", "instruction": "test"})
        assert result["workflow_type"] == "instruction"

        result = _convert_legacy_to_unified({"session_type": "scheduled", "instruction": "test"})
        assert result["workflow_type"] == "scheduled"

    def test_status_mapping(self):
        from distr.core.workflow.service import _convert_legacy_to_unified

        result = _convert_legacy_to_unified({"status": "planned", "instruction": "test"})
        assert result["status"] == "draft"

        result = _convert_legacy_to_unified({"status": "in_progress", "instruction": "test"})
        assert result["status"] == "active"

    def test_instruction_to_description(self):
        from distr.core.workflow.service import _convert_legacy_to_unified

        result = _convert_legacy_to_unified({"instruction": "Do the thing"})
        assert result["description"] == "Do the thing"

    def test_step_title_to_name(self):
        from distr.core.workflow.service import _convert_legacy_to_unified

        result = _convert_legacy_to_unified({
            "instruction": "test",
            "steps": [{"title": "My Step", "instruction": "do it", "position": 0}],
        })
        assert result["steps"][0]["name"] == "My Step"

    def test_schedule_fields_mapped(self):
        from distr.core.workflow.service import _convert_legacy_to_unified

        result = _convert_legacy_to_unified({
            "instruction": "test",
            "schedule": "daily",
            "schedule_time": "09:00",
            "schedule_days": "1,3,5",
            "enabled": True,
            "timezone": "America/New_York",
        })
        assert result["schedule_preset"] == "daily"
        assert result["schedule_time"] == "09:00"
        assert result["schedule_days"] == "1,3,5"
        assert result["schedule_enabled"] is True
        assert result["schedule_timezone"] == "America/New_York"
