"""Existing-first workflow selection and generated-definition quality gates."""

from __future__ import annotations

from distr.core.workflow.selection import (
    audit_workflow_contract,
    select_workflow_for_request,
    validate_generated_workflow_payload,
)


def _complete_workflow(workflow_id: int, name: str) -> dict:
    roles = ["planning", "planning", "implementation", "review", "implementation", "reporting"]
    steps = []
    for position, role in enumerate(roles):
        tools = ["cli", "shell"]
        if role == "review":
            tools += ["playwright", "browser_use"]
        steps.append({
            "position": position,
            "name": f"{role.title()} stage {position + 1}",
            "instruction": (
                "Load the required scoped context, perform this stage without scope creep, capture concrete evidence, "
                "and stop with an explicit blocker rather than claiming success without proof."
            ),
            "action_type": "send_to_project_cli",
            "validation_type": "llm_judgment",
            "validation_prompt": "Pass only when the named outputs and objective evidence are present and no blockers remain.",
            "on_pass_goto_position": position + 1 if position < len(roles) - 1 else None,
            "on_fail_goto_position": 4 if role == "review" else None,
            "config": {
                "step_role": role,
                "skills": ["systematic-debugging", "verification-loop"],
                "tools": tools,
                "guardrail": "Stay within the linked ticket and project scope; do not fabricate evidence.",
                "failure_checklist": ["Required evidence is missing", "Acceptance criteria are not met"],
                "required_context": ["ticket", "project", "project_memory", "workflow_memory"],
                "expected_outputs": ["result", "evidence", "memory_delta"],
                "model_policy": {"mode": "auto", "free_only": False, "prefer_local": True},
            },
        })
    return {
        "id": workflow_id,
        "format_version": "2.0",
        "name": name,
        "description": f"Complete {name} workflow with independent validation.",
        "context_rules": "Load ticket, project, memories, prior failures, and acceptance criteria for every step.",
        "run_settings": {
            "memory_enabled": True,
            "load_project_memory": True,
            "load_workflow_memory": True,
            "capture_memory_deltas": True,
            "capture_failures_and_lessons": True,
        },
        "steps": steps,
    }


def test_ui_specialist_outranks_generic_development_when_both_are_complete():
    development = _complete_workflow(1, "Development")
    ui = _complete_workflow(2, "UI Development")

    result = select_workflow_for_request(
        "Rebuild the responsive checkout UI and validate it in Playwright",
        candidates=[development, ui],
    )

    assert result["create_required"] is False
    assert result["selected"]["workflow_id"] == 2


def test_backend_specialist_outranks_generic_development_when_scope_matches():
    result = select_workflow_for_request(
        "Implement API endpoints and a database migration",
        candidates=[
            _complete_workflow(1, "Development"),
            _complete_workflow(3, "Backend API Development"),
        ],
    )
    assert result["selected"]["workflow_id"] == 3


def test_development_is_reused_for_normal_backend_or_single_line_ui_work():
    development = _complete_workflow(1, "Development")
    for request in ("Make the green button black", "Add a backend API migration"):
        result = select_workflow_for_request(request, candidates=[development])
        assert result["selected"]["workflow_id"] == 1


def test_unrelated_request_does_not_fall_into_development():
    result = select_workflow_for_request(
        "Send a weekly Gmail digest",
        candidates=[_complete_workflow(1, "Development")],
    )
    assert result["selected"] is None
    assert result["create_required"] is True


def test_generated_workflow_quality_gate_rejects_skeletal_steps():
    skeletal = {
        "name": "UI Workflow",
        "steps": [{"position": 0, "name": "Build", "instruction": "Build it"}],
    }
    audit = validate_generated_workflow_payload(skeletal, request_text="Build a frontend product")
    assert audit["viable"] is False
    assert any("purposeful steps" in gap for gap in audit["missing"])
    assert any("meaningful validation" in gap for gap in audit["missing"])


def test_complete_generated_workflow_contract_passes_quality_gate():
    workflow = _complete_workflow(4, "Specialized UI Development")
    audit = validate_generated_workflow_payload(
        workflow,
        request_text="Create a specialized UI workflow for a large product ticket group",
    )
    assert audit["viable"] is True
    assert audit_workflow_contract(
        workflow,
        request_profile={"software": True, "ui": True, "backend": False},
    )["quality_score"] == 100


def test_agent_generate_tool_reuses_viable_workflow_without_calling_generator(monkeypatch):
    from distr.core.agent.tools.step_runner.workflow_tools import GenerateWorkflowTool

    workflow = _complete_workflow(7, "Development")
    monkeypatch.setattr(
        "distr.core.workflow.selection.select_workflow_for_request",
        lambda description: {
            "selected": {"workflow_id": 7, "workflow_name": "Development"},
            "reason": "complete contract",
        },
    )
    monkeypatch.setattr("distr.core.workflow.service.get_workflow", lambda workflow_id: workflow)
    monkeypatch.setattr(
        "distr.core.workflow_engine.code_generator.CodeGeneratorService._call_coding_llm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("generator must not run")),
    )

    result = GenerateWorkflowTool()._run("Build a backend endpoint")

    assert "selected it instead of creating another workflow" in result
    assert "Development" in result
