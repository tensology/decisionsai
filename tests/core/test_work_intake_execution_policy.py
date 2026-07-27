from distr.core.work_intake.execution_policy import (
    apply_approved_provider_replacements_to_route,
    apply_requested_step_policy,
    compile_requested_execution_policy,
    infer_step_role,
)


def test_approved_provider_replacement_is_applied_before_next_group_preflight():
    route = {
        "backend": "pi",
        "model_provider": "openrouter",
        "model": "z-ai/glm-4.5-air:free",
        "complexity": "medium",
    }

    updated = apply_approved_provider_replacements_to_route(route, [{
        "from_backend": "pi",
        "from_model": "z-ai/glm-4.5-air:free",
        "to_backend": "codex",
        "to_model": "auto",
    }])

    assert updated == {
        "backend": "codex",
        "model": "auto",
        "complexity": "medium",
        "source": "approved_provider_replacement",
        "requires_approval": False,
    }
    assert route["model"] == "z-ai/glm-4.5-air:free"


REQUEST = (
    "Prefer local/free models for planning, use Codex for implementation, "
    "use a different model for review, ask before deployment, and update me here."
)


def test_compiles_explicit_channel_model_and_approval_policy():
    policy = compile_requested_execution_policy(REQUEST)

    assert policy["roles"]["planning"] == {
        "prefer_local": True,
        "free_only": True,
        "force_reselect": True,
    }
    assert policy["roles"]["implementation"]["backend"] == "codex"
    assert policy["roles"]["review"]["independent_from"] == "implementation"
    assert policy["approval_before_roles"] == ["deployment"]


def test_infers_existing_development_workflow_roles():
    assert infer_step_role({"name": "Plan and scope the ticket"}) == "planning"
    assert infer_step_role({"name": "Ingest ticket and project context"}) == "planning"
    assert infer_step_role({
        "name": "Write plan.md and attach to ticket",
        "instruction": "Attach the plan before implementation starts.",
    }) == "planning"
    assert infer_step_role({"name": "Implement approved changes"}) == "implementation"
    assert infer_step_role({"name": "Independent QA review"}) == "review"
    assert infer_step_role({"name": "Final production polish and ship audit"}) == "final_polish"
    assert infer_step_role({
        "name": "Release readiness review",
        "config": {"step_role": "final_polish"},
    }) == "final_polish"
    assert infer_step_role({
        "name": "Report, update ticket, and compact memory",
        "config": {"step_role": "reporting"},
    }) == "reporting"
    assert infer_step_role({"name": "Report and compact memory"}) == "reporting"
    assert infer_step_role({"name": "Deploy release"}) == "deployment"


def test_request_policy_can_swap_step_default_unless_route_is_locked():
    run_data = {"requested_execution_policy": compile_requested_execution_policy(REQUEST)}
    config, role, requested = apply_requested_step_policy(
        {"model": "auto"},
        step={"name": "Implement approved changes"},
        run_data=run_data,
    )
    assert role == "implementation"
    assert requested["backend"] == "codex"
    assert config["backend_id"] == "codex"

    swapped, _, _ = apply_requested_step_policy(
        {"backend_id": "claude_code", "model": "auto"},
        step={"name": "Implement approved changes"},
        run_data=run_data,
    )
    assert swapped["backend_id"] == "codex"

    pinned, _, _ = apply_requested_step_policy(
        {"backend_id": "claude_code", "model": "auto", "route_locked": True},
        step={"name": "Implement approved changes"},
        run_data=run_data,
    )
    assert pinned["backend_id"] == "claude_code"


def test_compiles_explicit_read_only_boundary():
    policy = compile_requested_execution_policy(
        "Inspect the project and verify the tests without editing any files."
    )

    assert policy["read_only"] is True


def test_named_read_only_test_suite_uses_verification_only_fast_path():
    policy = compile_requested_execution_policy(
        "Run tests/core/test_work_intake_execution_policy.py and report the exact "
        "result without editing project files."
    )

    assert policy["read_only"] is True
    assert policy["verification_only"] is True


def test_broad_read_only_audit_keeps_planning_and_reporting_steps():
    policy = compile_requested_execution_policy(
        "Audit the workflow architecture without modifying project files."
    )

    assert policy["read_only"] is True
    assert "verification_only" not in policy


def test_read_only_development_path_skips_mutation_but_not_failed_review():
    from distr.core.workflow.post_execution import _read_only_step_action

    assert _read_only_step_action(
        read_only=True,
        step_role="implementation",
        step_name="Implement the planned change",
        prior_step_passed=True,
    ) == "skip"
    assert _read_only_step_action(
        read_only=True,
        step_role="review",
        step_name="Independently review the result",
        prior_step_passed=True,
    ) == "execute"
    assert _read_only_step_action(
        read_only=True,
        step_role="planning",
        step_name="Understand the ticket",
        prior_step_passed=True,
        verification_only=True,
    ) == "skip"
    assert _read_only_step_action(
        read_only=True,
        step_role="reporting",
        step_name="Report and compact memory",
        prior_step_passed=True,
        verification_only=True,
    ) == "skip"
    assert _read_only_step_action(
        read_only=True,
        step_role="implementation",
        step_name="Correct defects found by validation",
        prior_step_passed=False,
    ) == "fail"
    assert _read_only_step_action(
        read_only=True,
        step_role="final_polish",
        step_name="Final production polish",
        prior_step_passed=True,
    ) == "skip"
    assert _read_only_step_action(
        read_only=True,
        step_role="reporting",
        step_name="Report and compact memory",
        prior_step_passed=True,
    ) == "execute"


def test_read_only_contract_survives_workflow_wording_and_durable_policy():
    from distr.core.workflow.step_executor import (
        _requested_execution_is_read_only,
        _ticket_requires_read_only_execution,
    )

    assert _ticket_requires_read_only_execution(
        "Perform a read-only workflow verification. Do not edit files."
    ) is True
    assert _requested_execution_is_read_only({
        "requested_execution_policy": {"read_only": True},
    }) is True
    assert _requested_execution_is_read_only(
        '{"requested_execution_policy":{"read_only":true}}'
    ) is True
