"""Managed class refresh is retired."""

from __future__ import annotations


def test_refresh_is_noop_after_managed_class_retirement():
    from distr.core.workflow import phases

    assert phases.refresh_managed_workflow_instructions() == {}
