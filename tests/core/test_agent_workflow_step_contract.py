from distr.core.agent.tools.step_runner import workflow_tools


def test_add_workflow_step_exposes_and_persists_full_execution_contract(monkeypatch):
    calls = {}

    def fake_add_step(workflow_id, **kwargs):
        calls["workflow_id"] = workflow_id
        calls["add"] = kwargs
        return 42

    def fake_update_step(step_id, **kwargs):
        calls["step_id"] = step_id
        calls["update"] = kwargs
        return True

    monkeypatch.setattr("distr.core.workflow.service.add_step", fake_add_step)
    monkeypatch.setattr("distr.core.workflow.service.update_step", fake_update_step)

    result = workflow_tools.AddWorkflowStepTool()._run(
        workflow_id=7,
        name="Independent validation",
        action_type="send_to_project_cli",
        instruction="Review the implementation independently.",
        backend_id="codex",
        model="gpt-5.3-codex",
        model_provider="openai",
        skills=["verification-loop"],
        tools=["cli", "playwright"],
        model_policy={"free_only": False, "prefer_local": False},
        required_context=["ticket", "implementation_result"],
        expected_outputs=["validation_report"],
        timeout_seconds=900,
        max_retries=2,
        require_approval=True,
    )

    assert "Added step" in result
    assert calls["workflow_id"] == 7
    config = calls["add"]["config"]
    assert config["backend_id"] == "codex"
    assert config["model"] == "gpt-5.3-codex"
    assert config["model_provider"] == "openai"
    assert config["skills"] == ["verification-loop"]
    assert config["tools"] == ["cli", "playwright"]
    assert config["required_context"] == ["ticket", "implementation_result"]
    assert config["expected_outputs"] == ["validation_report"]
    assert calls["update"] == {"max_retries": 2, "timeout_seconds": 900, "require_approval": True}


def test_update_workflow_step_merges_execution_config(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        workflow_tools,
        "_read_step_execution_config",
        lambda _step_id: {"guardrail": "Keep scope", "tools": ["cli"], "model": "auto"},
    )

    def fake_update_step(step_id, **kwargs):
        saved["step_id"] = step_id
        saved.update(kwargs)
        return True

    monkeypatch.setattr("distr.core.workflow.service.update_step", fake_update_step)
    result = workflow_tools.UpdateWorkflowStepTool()._run(
        step_id=99,
        model="claude-sonnet",
        backend_id="claude_code",
        skills=["code-review"],
        model_policy={"auto_route_models": False},
    )

    assert "Updated step" in result
    assert saved["step_id"] == 99
    config = saved["config"]
    assert config["guardrail"] == "Keep scope"
    assert config["tools"] == ["cli"]
    assert config["model"] == "claude-sonnet"
    assert config["backend_id"] == "claude_code"
    assert config["skills"] == ["code-review"]
    assert config["model_policy"] == {"auto_route_models": False}


def test_agent_step_schemas_match_runtime_configuration_fields():
    expected = {
        "config", "backend_id", "model", "model_provider", "complexity", "skills", "tools",
        "guardrail", "failure_checklist", "context", "required_context", "expected_outputs",
        "other_tool", "model_policy", "execution_route", "max_retries", "timeout_seconds",
        "require_approval",
    }
    assert expected <= set(workflow_tools.AddWorkflowStepInput.model_fields)
    assert expected <= set(workflow_tools.UpdateWorkflowStepInput.model_fields)


def test_current_development_bundle_preserves_nested_model_and_output_contracts():
    from distr.core.workflow.loop_preset_loader import load_bundle_by_slug, normalize_bundle_steps

    bundle = load_bundle_by_slug("development-ticket-to-implementation")
    steps = normalize_bundle_steps(bundle)
    assert steps
    first = steps[0]["config"]
    assert first["model_policy"] == {"mode": "auto", "free_only": True, "prefer_local": True}
    assert first["required_context"] == [
        "ticket", "board", "project", "workflow_memory", "project_memory", "linked_attachments"
    ]
    assert first["expected_outputs"] == [
        "context_packet", "unknowns", "route_recommendation", "ui_design_read_if_applicable"
    ]

    review = next(step for step in steps if step["title"] == "Independently review and validate the change")
    assert "project_release_findings" in review["config"]["expected_outputs"]
    assert "ship_verdict applies only to the linked ticket" in review["instruction"]
