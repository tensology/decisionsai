import asyncio
from types import SimpleNamespace

import pytest

from distr.core.project_cli_backends.base import BackendTaskResult, ProjectTask
from distr.core.project_cli_backends.contracts import (
    BackendCapabilities,
    normalize_execution_result,
)
from distr.core.workspace_memory.delta import normalize_memory_delta
from distr.core.project_cli_backends.model_policy import apply_workflow_model_policy
from distr.core.project_cli_backends.registry import (
    _BoundedCliOutput,
    OneShotCliBackend,
    PiBackend,
    _ONE_SHOT_PROCESSES,
    _execution_event_message,
    _is_duplicate_progress_event,
    _pi_print_command,
    _pi_workflow_report_error,
)
from distr.core.workflow.step_executor import StepExecutorMixin
from distr.core.workflow.verification import _run_verification


def test_cli_output_buffer_bounds_chatty_workers_and_preserves_completion_tail():
    output = _BoundedCliOutput(head_limit=8, tail_limit=12)
    output.append("HEADER--")
    output.append("x" * 100_000)
    output.append("STATUS:done")

    rendered = output.render()

    assert rendered.startswith("HEADER--")
    assert rendered.endswith("STATUS:done")
    assert "omitted" in rendered
    assert len(rendered) < 200


def test_backend_capabilities_are_name_not_backend_based():
    capabilities = BackendCapabilities(steering=True, files=True)
    assert capabilities.supports({"steering", "files"})
    assert not capabilities.supports({"steering", "images"})


def test_legacy_backend_result_normalizes_to_provider_neutral_contract():
    raw = BackendTaskResult(
        success=True,
        backend_id="anything",
        engine="custom_transport",
        output="Implemented the requested change.",
        execution_session_id=41,
    )
    result = normalize_execution_result(raw, attempt_id=41)
    assert result.status == "completed"
    assert result.backend_id == "anything"
    assert result.attempt_id == 41
    assert result.summary == "Implemented the requested change."


def test_waiting_result_normalizes_independently_of_harness_name():
    result = normalize_execution_result({
        "success": True,
        "waits_for_human": True,
        "output": "Please approve deployment.",
    }, backend_id="future_backend")
    assert result.status == "waiting"
    assert result.waits_for_human is True


def test_provider_options_are_opaque_with_legacy_bridge():
    task = ProjectTask(
        project_id=1,
        project_name="Pizza House",
        folder="/tmp/pizza-house",
        instruction="Build the menu.",
        codex_reasoning_effort="high",
        adapter_options={"provider": "local"},
    )
    assert task.adapter_options == {"provider": "local", "reasoning_effort": "high"}


def test_memory_delta_is_stable_across_raw_backend_shapes():
    first = normalize_memory_delta({
        "summary": "Menu implemented",
        "files_changed": "src/menu.ts",
        "tests_run": "npm test",
        "blockers": "none",
    })
    second = normalize_memory_delta({
        "summary": "Menu implemented",
        "changed_files": ["src/menu.ts"],
        "evidence": ["npm test"],
        "blockers": ["none"],
    })
    assert first.to_dict() == second.to_dict()


def test_memory_projection_does_not_include_provider_or_model():
    delta = normalize_memory_delta(
        {"summary": "Checkout validated", "decisions": ["Use card payments"]},
        provenance={"run_id": 9, "step_id": 3},
    )
    markdown = delta.to_markdown()
    assert "Checkout validated" in markdown
    assert "Use card payments" in markdown
    assert "codex" not in markdown.lower()


def test_free_policy_preserves_explicit_board_scoped_local_model():
    route = {
        "backend": "pi",
        "model": "ornith:35b",
        "source": "board_override",
        "complexity": "medium",
    }
    resolved = apply_workflow_model_policy(
        route,
        workflow=type("Workflow", (), {"run_settings": '{"free_only": true, "prefer_local": true}'})(),
        config={},
        settings={},
    )
    assert resolved["backend"] == "pi"
    assert resolved["model"] == "ornith:35b"
    assert resolved["policy_source"] == "board_override_preserved"


def test_free_policy_does_not_replace_concrete_model_when_source_metadata_is_missing():
    resolved = apply_workflow_model_policy(
        {"backend": "pi", "model": "ornith:35b"},
        workflow=type("Workflow", (), {"run_settings": '{"free_only": true}'})(),
        config={"model_policy": {"prefer_local": True}},
        settings={},
    )
    assert resolved["model"] == "ornith:35b"
    assert resolved["policy_source"] == "selected_route_preserved"


def test_auto_policy_does_not_pair_pi_catalog_model_with_explicit_codex_backend():
    resolved = apply_workflow_model_policy(
        {
            "backend": "codex",
            "model": "auto",
            "source": "board_override",
            "complexity": "high",
        },
        workflow=type("Workflow", (), {"run_settings": '{"auto_route_models": true}'})(),
        config={"backend_id": "codex", "model": "auto"},
        settings={},
    )
    assert resolved["backend"] == "codex"
    assert resolved["model"] == "auto"
    assert resolved["policy_source"] == "board_override_native_auto_preserved"
    assert "model_provider" not in resolved


def test_auto_policy_preserves_board_scoped_non_pi_backend_without_step_override():
    resolved = apply_workflow_model_policy(
        {"backend": "claude_code", "model": "auto", "source": "board_override"},
        workflow=type("Workflow", (), {"run_settings": '{}'})(),
        config={},
        settings={},
    )
    assert resolved["backend"] == "claude_code"
    assert resolved["model"] == "auto"
    assert resolved["policy_source"] == "board_override_native_auto_preserved"


def test_one_shot_backend_cancellation_terminates_and_unregisters_process(tmp_path):
    class SlowBackend(OneShotCliBackend):
        id = "slow_test"
        name = "Slow test backend"

        def setup_status(self):
            return SimpleNamespace(ready=True, path="/bin/sh")

        def _build_command(self, executable, task):
            return [executable, "-c", "exec sleep 30"]

    task = ProjectTask(
        project_id=991,
        project_name="Timeout fixture",
        folder=str(tmp_path),
        instruction="wait",
        board_id=19,
    )

    async def run_with_timeout():
        await asyncio.wait_for(SlowBackend().send_task(task), timeout=0.1)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run_with_timeout())

    assert (991, "slow_test", 19) not in _ONE_SHOT_PROCESSES


def test_large_worker_diff_is_summarized_for_mission_control_feed():
    message = _execution_event_message(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "diff --git a/app.js b/app.js\n@@ -1 +1 @@\n-old\n+new\n+more",
            },
        }
    )
    assert message == "Worker is updating project files."


def test_short_natural_worker_progress_is_preserved():
    message = _execution_event_message(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "Running the browser validation now.",
            },
        }
    )
    assert message == "Running the browser validation now."


def test_large_single_line_worker_context_is_summarized():
    message = _execution_event_message(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "const state = JSON.parse(readFileSync('state.json')); " * 20,
            },
        }
    )
    assert message == "Worker is processing project context."


def test_duplicate_worker_progress_burst_is_coalesced_but_lifecycle_is_not():
    message = "Worker is updating project files."
    assert _is_duplicate_progress_event(
        {"type": "message_update"},
        message,
        previous_message=message,
        previous_at=10.0,
        now=10.2,
    )
    assert not _is_duplicate_progress_event(
        {"type": "agent_end"},
        message,
        previous_message=message,
        previous_at=10.0,
        now=10.2,
    )
    assert not _is_duplicate_progress_event(
        {"type": "message_update"},
        message,
        previous_message=message,
        previous_at=10.0,
        now=11.1,
    )


def test_pi_workflow_command_honours_selected_local_provider_and_model():
    task = ProjectTask(
        project_id=12,
        project_name="Ember & Crust Pizza House",
        folder="/tmp/pizza-house",
        instruction="Scope the landing page.",
        origin="workflow",
        model="ornith:35b",
        adapter_options={"model_provider": "ollama"},
    )
    command = _pi_print_command("/usr/local/bin/pi", task)
    assert command[:6] == [
        "/usr/local/bin/pi", "-p", "--provider", "ollama", "--model", "ornith:35b"
    ]
    assert command[-1] == "Scope the landing page."


def test_pi_workflow_command_honours_provider_for_nested_catalog_model():
    task = ProjectTask(
        project_id=12,
        project_name="Ember & Crust Pizza House",
        folder="/tmp/pizza-house",
        instruction="Review the implementation.",
        model="openrouter/free",
        adapter_options={"model_provider": "kilocode"},
    )
    command = _pi_print_command("pi", task)
    assert command[command.index("--provider") + 1] == "kilocode"
    assert command[command.index("--model") + 1] == "openrouter/free"


def test_pi_empty_success_is_classified_as_failed_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    task = ProjectTask(
        project_id=12,
        project_name="Pizza House",
        folder=str(tmp_path),
        instruction="Implement the menu.",
        origin="workflow",
        model="openrouter/free",
        adapter_options={"model_provider": "kilocode"},
    )

    result = asyncio.run(PiBackend().send_task(task))

    assert result.success is False
    assert "no-op" in result.error


def test_pi_workflow_report_contract_rejects_unverified_or_failed_text():
    assert "required" in _pi_workflow_report_error("Should I proceed with the work?")
    assert "failed" in _pi_workflow_report_error("Status: failed\nSummary: test failed")
    assert "needs_input" in _pi_workflow_report_error("Status: needs_input\nBlockers: missing file")
    assert _pi_workflow_report_error("Status: completed\nSummary: shipped") == ""


def test_step_auto_model_inherits_concrete_board_route():
    route = {"backend": "pi", "model": "ornith:35b", "source": "board_override"}
    merged = StepExecutorMixin._apply_step_harness_overrides(route, {"model": "auto"})
    assert merged["model"] == "ornith:35b"


def test_unavailable_llm_judge_preserves_explicit_success(monkeypatch):
    monkeypatch.setattr(
        "distr.core.orchestrator_validator.run_orchestrator_validator_judgment",
        lambda **kwargs: None,
    )
    step = type("Step", (), {
        "id": 1,
        "validation_type": "llm_judgment",
        "validation_prompt": "Ticket context is explicit.",
    })()
    assert _run_verification(step, "Status: completed\nTicket and route are explicit.", True)
    assert not _run_verification(step, "Status: failed", False)


def test_backend_registry_has_module_logger_for_heartbeat_diagnostics():
    from distr.core.project_cli_backends import registry

    assert registry.logger.name == "distr.core.project_cli_backends.registry"


def test_project_cli_prompt_compaction_preserves_identity_and_current_step():
    prompt = "IDENTITY\n" + ("old history\n" * 5000) + "CURRENT STEP AND RETURN CONTRACT"
    compact = StepExecutorMixin._bound_project_cli_prompt(prompt, max_chars=1200)

    assert len(compact) <= 1200
    assert compact.startswith("IDENTITY")
    assert compact.endswith("CURRENT STEP AND RETURN CONTRACT")
    assert "Historical context compacted for worker latency" in compact


def test_live_harness_accepts_pi_dynamic_model_for_auto_policy(monkeypatch):
    from scripts.workflow_ticket_loop_e2e import WorkflowTicketLoopHarness

    harness = WorkflowTicketLoopHarness("http://127.0.0.1:8765/api")
    monkeypatch.setattr(
        harness,
        "api_request",
        lambda _path: {
            "sessions": [{
                "workflow_id": 4,
                "run_id": 9,
                "route_backend": "pi",
                "selected_model": "openrouter/free",
                "complexity": "high",
            }],
        },
    )

    session = harness.assert_execution_session_for_ticket(
        ticket_id=3,
        workflow_id=4,
        run_id=9,
        backend_id="pi",
        expected_model="auto",
        complexity="high",
    )

    assert session["selected_model"] == "openrouter/free"


def test_runtime_provider_failover_defaults_between_pi_and_codex():
    assert StepExecutorMixin._runtime_provider_fallback_route(
        {"backend": "pi", "model": "openrouter/free"},
        {},
    ) == {"backend": "codex", "model": "auto"}
    assert StepExecutorMixin._runtime_provider_fallback_route(
        {"backend": "codex", "model": "auto"},
        {},
    ) == {"backend": "pi", "model": "auto"}


@pytest.mark.parametrize("message", ["Credit balance is too low", "Insufficient credits"])
def test_provider_billing_messages_fail_closed(message):
    from distr.core.workflow.step_executor import _agent_result_passed

    assert _agent_result_passed(message) is False


def test_runtime_provider_failover_can_be_disabled_or_scoped():
    assert StepExecutorMixin._runtime_provider_fallback_route(
        {"backend": "pi"},
        {"allow_provider_failover": False},
    ) == {}
    assert StepExecutorMixin._runtime_provider_fallback_route(
        {"backend": "pi"},
        {
            "fallback_backend": "claude_code",
            "fallback_model": "sonnet",
            "fallback_model_provider": "anthropic",
        },
    ) == {
        "backend": "claude_code",
        "model": "sonnet",
        "model_provider": "anthropic",
    }


@pytest.mark.parametrize("backend_class", ["CodexBackend", "CursorBackend"])
def test_cli_callback_token_is_not_exposed_in_process_arguments(
    monkeypatch,
    tmp_path,
    backend_class,
):
    from distr.core.project_cli_backends import registry

    monkeypatch.setattr(
        registry,
        "_with_internal_token",
        lambda url: f"{url}?internal_token=super-secret",
    )
    task = ProjectTask(
        project_id=4,
        project_name="Pizza House",
        folder=str(tmp_path),
        instruction="Implement the menu.",
        origin="workflow",
        workflow_id=8,
        run_id=9,
    )
    backend = getattr(registry, backend_class)()

    command = backend._build_command(backend.id, task)
    environment = backend._task_subprocess_env(task)

    assert "super-secret" not in " ".join(command)
    assert "$DECISIONS_CALLBACK_URL" in command[-1]
    assert environment["DECISIONS_CALLBACK_URL"].endswith("internal_token=super-secret")
