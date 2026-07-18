import asyncio
import time
from types import SimpleNamespace

import pytest

from distr.core.project_cli_backends.base import BackendTaskResult, ProjectTask
from distr.core.project_cli_backends.contracts import (
    BackendCapabilities,
    normalize_execution_result,
)
from distr.core.workspace_memory.delta import normalize_memory_delta
from distr.core.project_cli_backends.model_policy import (
    apply_auto_step_role_policy,
    apply_workflow_model_policy,
    build_auto_fallback_chain,
)
from distr.core.project_cli_backends.registry import (
    _BoundedCliOutput,
    OneShotCliBackend,
    OpenCodeBackend,
    PiBackend,
    _ONE_SHOT_PROCESSES,
    _execution_event_message,
    _is_duplicate_progress_event,
    _pi_print_command,
    _pi_workflow_report_error,
)
from distr.core.workflow.step_executor import StepExecutorMixin
from distr.core.workflow.verification import _run_verification
from distr.core.kanban.ticket_workflow_engagement import build_route_selection_message


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


def test_worker_report_normalizes_artifacts_memory_diagnostics_and_next_actions():
    result = normalize_execution_result(
        BackendTaskResult(
            success=True,
            backend_id="pi",
            engine="pi",
            output=(
                "Status: completed\n"
                "Summary: Added the menu route.\n"
                "Files changed: src/menu.py\n"
                "Tests: pytest -q\n"
                "Evidence: 4 passed\n"
                "Blockers: none\n"
                "Next step: Validate the browser journey."
            ),
        )
    )

    assert result.artifacts == [{"type": "changed_files", "value": "src/menu.py"}]
    assert result.memory_delta["summary"] == "Added the menu route."
    assert result.memory_delta["changed_files"] == ["src/menu.py"]
    assert result.memory_delta["evidence"] == ["pytest -q", "4 passed"]
    assert result.diagnostics == {"backend_id": "pi", "engine": "pi"}
    assert result.next_actions == {"recommended": ["Validate the browser journey."]}


def test_markdown_worker_report_is_accepted_and_normalized():
    output = (
        "**Status:** completed\n"
        "**Summary:** Created the proof.\n"
        "**Files changed:** proof.txt\n"
        "**Next step:** Validate it."
    )
    assert _pi_workflow_report_error(output) == ""
    result = normalize_execution_result(
        BackendTaskResult(success=True, backend_id="pi", engine="pi_cli", output=output)
    )
    assert result.memory_delta["summary"] == "Created the proof."
    assert result.artifacts == [{"type": "changed_files", "value": "proof.txt"}]
    assert result.next_actions == {"recommended": ["Validate it."]}

    alternate = output.replace("**Status:**", "**Status**:")
    assert _pi_workflow_report_error(alternate) == ""


def test_semicolon_worker_report_is_normalized():
    output = (
        "Status: completed; Summary: Created the proof; Files changed: proof.txt; "
        "Tests: exact comparison passed; Evidence: proof.txt; Blockers: none; "
        "Next step: Validate it.\nWarning: custom model id"
    )
    result = normalize_execution_result(
        BackendTaskResult(success=True, backend_id="pi", engine="pi_cli", output=output)
    )
    assert result.memory_delta == {
        "summary": "Created the proof",
        "changed_files": ["proof.txt"],
        "evidence": ["exact comparison passed", "proof.txt"],
        "blockers": ["none"],
    }
    assert result.artifacts == [{"type": "changed_files", "value": "proof.txt"}]
    assert result.next_actions == {"recommended": ["Validate it."]}


def test_markdown_table_worker_report_is_accepted_and_normalized():
    output = (
        "| Field | Value | Explanation |\n"
        "|---|---|---|\n"
        "| **Status** | `completed` | Work finished |\n"
        "| **Summary** | Created the proof | Done |\n"
        "| **Files changed** | `proof.txt` | One file |\n"
        "| **Tests** | exact comparison passed | Verified |\n"
        "| **Evidence** | `proof.txt` | Inspect it |\n"
        "| **Blockers** | none | Clear |\n"
        "| **Next step** | Validate it | Continue |"
    )
    assert _pi_workflow_report_error(output) == ""
    result = normalize_execution_result(
        BackendTaskResult(success=True, backend_id="pi", engine="pi_cli", output=output)
    )
    assert result.memory_delta == {
        "summary": "Created the proof",
        "changed_files": ["proof.txt"],
        "evidence": ["exact comparison passed", "proof.txt"],
        "blockers": ["none"],
    }
    assert result.artifacts == [{"type": "changed_files", "value": "proof.txt"}]
    assert result.next_actions == {"recommended": ["Validate it"]}


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


def test_auto_policy_preserves_resolved_codex_complexity_route():
    resolved = apply_workflow_model_policy(
        {"backend": "codex", "model": "auto", "source": "policy", "complexity": "medium"},
        workflow=type("Workflow", (), {"run_settings": '{}'})(),
        config={},
        settings={},
    )

    assert resolved["backend"] == "codex"
    assert resolved["model"] == "auto"
    assert resolved["policy_source"] == "policy_native_auto_preserved"
    assert "model_provider" not in resolved


def test_explicit_prefer_local_policy_can_reselect_resolved_codex_route(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "ornith:9b", "provider": "ollama", "local": True, "free": True},
        ],
    )
    resolved = apply_workflow_model_policy(
        {"backend": "codex", "model": "auto", "source": "policy", "complexity": "medium"},
        workflow=type("Workflow", (), {"run_settings": '{"prefer_local": true}'})(),
        config={},
        settings={},
    )

    assert resolved["backend"] == "pi"
    assert resolved["model"] == "ornith:9b"


def test_explicit_codex_step_is_not_given_an_ollama_model_by_workflow_preference(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "ornith:9b", "provider": "ollama", "local": True, "free": True},
        ],
    )
    resolved = apply_workflow_model_policy(
        {"backend": "codex", "model": "auto", "complexity": "medium"},
        workflow=type("Workflow", (), {"run_settings": '{"prefer_local": true}'})(),
        config={"backend_id": "codex", "model": "auto"},
        settings={},
    )

    assert resolved["backend"] == "codex"
    assert resolved["model"] == "auto"
    assert resolved["policy_source"] == "step_backend_native_auto_preserved"


def test_auto_step_policy_routes_medium_implementation_to_configured_ornith(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "ornith:35b", "provider": "ollama", "local": True, "free": True},
        ],
    )
    resolved = apply_auto_step_role_policy(
        {"backend": "codex", "model": "auto", "complexity": "medium"},
        workflow=type("Workflow", (), {"run_settings": '{"auto_route_models": true}'})(),
        config={"model": "auto"},
        settings={"coding_llm_provider": "ollama", "coding_llm_model": "ornith:35b"},
        step_role="implementation",
    )

    assert resolved["backend"] == "pi"
    assert resolved["model"] == "ornith:35b"
    assert resolved["model_provider"] == "ollama"
    assert resolved["auto_detected"] is True
    assert resolved["step_role"] == "implementation"
    assert resolved["fallback_chain"][0]["backend"] == "codex"


def test_auto_step_policy_can_plan_with_codex_then_implement_with_ornith(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "ornith:35b", "provider": "ollama", "local": True, "free": True},
        ],
    )
    workflow = type("Workflow", (), {"run_settings": '{"auto_route_models": true}'})()
    settings = {"coding_llm_provider": "ollama", "coding_llm_model": "ornith:35b"}

    planning = apply_auto_step_role_policy(
        {"backend": "pi", "model": "auto", "complexity": "medium"},
        workflow=workflow,
        config={"model": "auto"},
        settings=settings,
        step_role="planning",
    )
    implementation = apply_auto_step_role_policy(
        {"backend": "pi", "model": "auto", "complexity": "medium"},
        workflow=workflow,
        config={"model": "auto"},
        settings=settings,
        step_role="implementation",
        prior_role_routes={"planning": planning},
    )

    assert planning["backend"] == "codex"
    assert implementation["backend"] == "pi"
    assert implementation["model"] == "ornith:35b"
    assert implementation["model_provider"] == "ollama"


def test_auto_step_policy_promotes_high_consequence_implementation_to_codex():
    resolved = apply_auto_step_role_policy(
        {
            "backend": "pi",
            "model": "auto",
            "complexity": "medium",
            "task_profile": {"intent": "implementation", "risk_flags": ["payments"]},
        },
        workflow=type("Workflow", (), {"run_settings": '{"auto_route_models": true}'})(),
        config={"model": "auto"},
        settings={},
        step_role="implementation",
    )

    assert resolved["backend"] == "codex"
    assert "high-consequence" in resolved["policy_reason"]


def test_auto_step_policy_uses_independent_hy3_review_after_codex():
    resolved = apply_auto_step_role_policy(
        {"backend": "codex", "model": "auto", "complexity": "high"},
        workflow=type("Workflow", (), {"run_settings": '{"auto_route_models": true}'})(),
        config={"model": "auto"},
        settings={"openrouter_enabled": True, "openrouter_key": "configured"},
        step_role="review",
        prior_role_routes={"implementation": {"backend": "codex", "model": "auto"}},
    )

    assert resolved["backend"] == "pi"
    assert resolved["model"] == "tencent/hy3-preview"
    assert resolved["model_provider"] == "openrouter"


def test_auto_step_policy_does_nothing_when_auto_detection_is_off(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [{"id": "ornith:35b", "provider": "ollama", "local": True}],
    )
    resolved = apply_auto_step_role_policy(
        {"backend": "codex", "model": "auto", "complexity": "medium"},
        workflow=type("Workflow", (), {"run_settings": '{"auto_route_models": false}'})(),
        config={"model": "auto"},
        settings={},
        step_role="implementation",
    )
    assert resolved == {"backend": "codex", "model": "auto", "complexity": "medium"}


def test_auto_step_policy_preserves_evidence_backed_failover_on_loop_retry():
    resolved = apply_auto_step_role_policy(
        {
            "backend": "codex",
            "model": "auto",
            "complexity": "medium",
            "source": "runtime_provider_failover",
            "fallback_from": "pi",
        },
        workflow=type("Workflow", (), {"run_settings": '{"auto_route_models": true}'})(),
        config={"model": "auto"},
        settings={"coding_llm_provider": "ollama", "coding_llm_model": "ornith:9b"},
        step_role="implementation",
    )

    assert resolved["backend"] == "codex"
    assert resolved["source"] == "runtime_provider_failover"
    assert resolved["fallback_from"] == "pi"


def test_auto_fallback_ladder_keeps_claude_last():
    chain = build_auto_fallback_chain(
        {"backend": "pi", "model": "ornith:35b", "model_provider": "ollama"},
        settings={"openrouter_enabled": True, "openrouter_key": "configured"},
    )
    assert [item["backend"] for item in chain] == ["codex", "cursor", "pi", "claude_code"]
    assert chain[2]["model"] == "tencent/hy3-preview"
    assert chain[-1]["backend"] == "claude_code"


def test_route_selection_message_names_model_role_and_reason_plainly():
    message = build_route_selection_message(
        ticket_title="Pizza House menu integrity",
        step_name="Implement menu validation",
        step_role="implementation",
        backend="pi",
        model="ornith:35b",
        provider="ollama",
        reason="Auto selected the configured local model for medium complexity.",
    )

    assert "Ornith 35B, running locally" in message
    assert "for the implementation" in message
    assert "medium complexity" in message
    assert "route" not in message.lower()


def test_route_selection_message_hides_internal_route_jargon_and_speaks_understand_step():
    message = build_route_selection_message(
        ticket_title="Research Kayla",
        step_name="Understand ticket and acceptance criteria",
        step_role="planning",
        backend="pi",
        model="nvidia/nemotron:free",
        provider="openrouter",
        reason="Workflow step selected scoped route nvidia/nemotron:free.",
    )

    assert "scoped route" not in message
    assert "reviewing the ticket requirements" in message


def test_prefer_local_policy_honors_configured_ollama_coding_model(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "codegemma:2b", "provider": "ollama", "local": True, "free": True},
            {"id": "ornith:9b", "provider": "ollama", "local": True, "free": True},
        ],
    )
    resolved = apply_workflow_model_policy(
        {"backend": "pi", "model": "auto", "complexity": "medium"},
        workflow=type("Workflow", (), {"run_settings": '{"prefer_local": true}'})(),
        config={},
        settings={
            "coding_llm_provider": "ollama",
            "coding_llm_model": "ornith:9b",
        },
    )
    assert resolved["backend"] == "pi"
    assert resolved["model"] == "ornith:9b"
    assert resolved["model_provider"] == "ollama"
    assert resolved["policy_reason"] == "Selected the configured local Ollama coding model."


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
    system_prompt = command[command.index("--append-system-prompt") + 1]
    assert "Status: completed | failed | needs_input" in system_prompt
    assert "Do not omit Status" in system_prompt
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


def test_long_running_local_pi_worker_emits_heartbeat_without_blocking_event_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )

    def slow_worker(*_args, **_kwargs):
        time.sleep(10.2)
        return SimpleNamespace(returncode=0, stdout="Status: completed\nSummary: local work finished", stderr="")

    monkeypatch.setattr("distr.core.project_cli_backends.registry.subprocess.run", slow_worker)
    task = ProjectTask(
        project_id=13,
        project_name="Responsive local workflow",
        folder=str(tmp_path),
        instruction="Complete a local code task.",
        origin="workflow",
        model="ornith:9b",
        adapter_options={"model_provider": "ollama"},
    )
    events: list[dict] = []

    async def run_worker() -> tuple[BackendTaskResult, int]:
        pending = asyncio.create_task(PiBackend().send_task(task, events.append))
        event_loop_ticks = 0
        while not pending.done():
            await asyncio.sleep(0.05)
            event_loop_ticks += 1
        return await pending, event_loop_ticks

    result, event_loop_ticks = asyncio.run(run_worker())

    assert result.success is True
    assert event_loop_ticks >= 100
    heartbeats = [event for event in events if event.get("type") == "heartbeat"]
    assert heartbeats
    assert heartbeats[-1]["backend"] == "pi"
    assert heartbeats[-1]["model"] == "ornith:9b"


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


def test_browser_ui_validation_is_a_supported_playwright_alias(monkeypatch):
    monkeypatch.setattr(
        "distr.core.workflow.verification._verify_playwright",
        lambda step, caller_passed, base_url="": caller_passed,
    )
    step = type("Step", (), {
        "id": 1,
        "validation_type": "browser_ui",
        "validation_prompt": "The browser journey passed.",
    })()
    assert _run_verification(step, "Browser journey passed.", True)


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
    ) == {"backend": "claude_code", "model": "auto"}


def test_runtime_provider_failover_uses_auto_chain_and_skips_interactive_cursor():
    route = {
        "backend": "codex",
        "model": "auto",
        "fallback_chain": [
            {"backend": "cursor", "model": "auto", "automatic": False},
            {
                "backend": "pi",
                "model": "tencent/hy3-preview",
                "model_provider": "openrouter",
                "automatic": True,
            },
            {"backend": "claude_code", "model": "auto", "automatic": True},
        ],
    }
    assert StepExecutorMixin._runtime_provider_fallback_route(route, {}) == {
        "backend": "pi",
        "model": "tencent/hy3-preview",
        "model_provider": "openrouter",
    }


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


def test_step_override_preserves_specific_provider_and_model():
    route = StepExecutorMixin._apply_step_harness_overrides(
        {"backend": "codex", "model": "auto"},
        {
            "backend_id": "pi",
            "model": "tencent/hy3-preview",
            "model_provider": "openrouter",
        },
    )
    assert route["backend"] == "pi"
    assert route["model"] == "tencent/hy3-preview"
    assert route["model_provider"] == "openrouter"


def test_opencode_translates_kilocode_route_and_scopes_project_folder(tmp_path):
    backend = OpenCodeBackend()
    task = ProjectTask(
        project_id=7,
        project_name="Pizza House",
        folder=str(tmp_path),
        instruction="Implement the ticket.",
        origin="workflow",
        model="openrouter/free",
        adapter_options={"model_provider": "kilocode"},
    )

    command = backend._build_command("opencode", task)

    assert command == [
        "opencode",
        "run",
        "--dir",
        str(tmp_path),
        "-m",
        "kilo/openrouter/free",
        "Implement the ticket.",
    ]


def test_opencode_workflow_rejects_success_without_completion_report(monkeypatch, tmp_path):
    async def fake_send_task(self, task, on_event=None):
        return BackendTaskResult(True, "opencode", "opencode", output="")

    monkeypatch.setattr(OneShotCliBackend, "send_task", fake_send_task)
    task = ProjectTask(
        project_id=7,
        project_name="Pizza House",
        folder=str(tmp_path),
        instruction="Implement the ticket.",
        origin="workflow",
    )

    result = asyncio.run(OpenCodeBackend().send_task(task))

    assert result.success is False
    assert result.error.startswith("OpenCode exited successfully but returned no completion report")


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
