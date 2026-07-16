from distr.core.work_intake.execution_policy import (
    apply_requested_step_policy,
    compile_requested_execution_policy,
    infer_step_role,
)


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
