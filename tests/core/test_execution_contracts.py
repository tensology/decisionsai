import asyncio
import json
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
    PI_JSONL_STREAM_LIMIT,
    PiBackend,
    _ONE_SHOT_PROCESSES,
    _execution_event_message,
    _is_duplicate_progress_event,
    _pi_print_command,
    _pi_workflow_report_error,
    _preferred_model_error,
    _workspace_state_delta,
    _workspace_state_snapshot,
)
from distr.core.workflow.step_executor import (
    StepExecutorMixin,
    _exclude_failed_route_candidates,
    _paid_fallback_requires_approval,
    _route_required_capabilities,
    _workflow_run_cancelled,
)
from distr.core.workflow.verification import _run_verification
from distr.core.kanban.ticket_workflow_engagement import build_route_selection_message


async def _async_value(value):
    return value


def test_cancelled_run_is_detected_before_provider_fallback(monkeypatch):
    db = SimpleNamespace()
    query = SimpleNamespace()
    filtered = SimpleNamespace(scalar=lambda: "cancelled")
    query.filter = lambda *_args, **_kwargs: filtered
    db.query = lambda *_args, **_kwargs: query

    class SessionContext:
        def __enter__(self):
            return db

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "distr.core.workflow.step_executor.get_session",
        lambda: SessionContext(),
    )

    assert _workflow_run_cancelled(112) is True


def test_free_local_route_requires_approval_before_paid_codex_fallback():
    assert _paid_fallback_requires_approval(
        {"backend": "pi", "model_provider": "ollama", "model": "ornith:35b"},
        {"backend": "codex", "model": "auto"},
        {},
    ) is True
    assert _paid_fallback_requires_approval(
        {"backend": "pi", "model_provider": "openrouter", "model": "free/model:free"},
        {"backend": "pi", "model_provider": "openrouter", "model": "other/model:free"},
        {},
    ) is False


def test_failed_readiness_model_is_not_recommended_again():
    candidates = [
        {"model": "google/gemma-4-31b-it:free", "name": "Gemma"},
        {"model": "cohere/north-mini-code:free", "name": "North"},
    ]

    assert _exclude_failed_route_candidates(
        candidates,
        failed_model="GOOGLE/GEMMA-4-31B-IT:FREE",
    ) == [{"model": "cohere/north-mini-code:free", "name": "North"}]


def test_all_previously_failed_models_are_excluded_from_retry_catalog():
    candidates = [
        {"model": "google/gemma-4-31b-it:free"},
        {"model": "google/gemma-4-26b-a4b-it:free"},
        {"model": "cohere/north-mini-code:free"},
    ]

    assert _exclude_failed_route_candidates(
        candidates,
        failed_models=[
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
        ],
    ) == [{"model": "cohere/north-mini-code:free"}]
    assert _paid_fallback_requires_approval(
        {"backend": "pi", "model_provider": "ollama", "model": "ornith:35b"},
        {"backend": "codex", "model": "auto"},
        {"auto_approve_paid_failover": True},
    ) is False


class _FakeAsyncStream:
    def __init__(self, owner, lines, first_line_delay=0.0):
        self.owner = owner
        self.lines = [line.encode() + b"\n" for line in lines]
        self.ready_at = time.monotonic() + first_line_delay

    async def readline(self):
        if self.lines:
            remaining = self.ready_at - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            return self.lines.pop(0)
        self.owner.returncode = 0
        return b""


class _FakeAsyncProcess:
    def __init__(self, lines, first_line_delay=0.0):
        self.returncode = None
        self.stdout = _FakeAsyncStream(self, lines, first_line_delay)

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class _TerminalEventStream:
    """Fail if the backend reads beyond Pi's terminal protocol event."""

    def __init__(self, lines):
        self.lines = [line.encode() + b"\n" for line in lines]

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        raise AssertionError("readline called after agent_end")


class _TerminalEventProcess(_FakeAsyncProcess):
    def __init__(self, lines):
        self.returncode = None
        self.stdout = _TerminalEventStream(lines)


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


def test_read_only_workspace_snapshot_detects_writes_inside_untracked_directories(tmp_path):
    copied_tree = tmp_path / "backend" / "webapp"
    copied_tree.mkdir(parents=True)
    existing = copied_tree / "settings.py"
    existing.write_text("DEBUG = True\n", encoding="utf-8")
    before = _workspace_state_snapshot(str(tmp_path))

    (copied_tree / "__init__.py").write_text("", encoding="utf-8")
    existing.write_text("DEBUG = False\n", encoding="utf-8")
    after = _workspace_state_snapshot(str(tmp_path))
    delta = _workspace_state_delta(before, after)

    assert delta["changed"] is True
    assert "backend/webapp/__init__.py" in delta["added"]
    assert "backend/webapp/settings.py" in delta["modified"]
    assert delta["total_changed"] == 2


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


def test_auto_step_policy_honors_free_local_preference_before_paid_high_risk_escalation(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "ornith:35b", "provider": "ollama", "local": True, "free": True},
            {"id": "free-planner", "provider": "openrouter", "local": False, "free": True},
        ],
    )
    workflow = type(
        "Workflow",
        (),
        {"run_settings": '{"auto_route_models": true, "prefer_free_local": true}'},
    )()
    settings = {"coding_llm_provider": "ollama", "coding_llm_model": "ornith:35b"}

    planning = apply_auto_step_role_policy(
        {"backend": "codex", "model": "auto", "complexity": "high"},
        workflow=workflow,
        config={"model": "auto"},
        settings=settings,
        step_role="planning",
    )
    implementation = apply_auto_step_role_policy(
        {
            "backend": "codex",
            "model": "auto",
            "complexity": "high",
            "task_profile": {"intent": "planning", "risk_flags": ["payments"]},
        },
        workflow=workflow,
        config={"model": "auto"},
        settings=settings,
        step_role="implementation",
    )

    assert planning["backend"] == "pi"
    assert planning["model"] == "ornith:35b"
    assert planning["model_provider"] == "ollama"
    assert implementation["backend"] == "pi"
    assert implementation["model"] == "ornith:35b"
    assert implementation["model_provider"] == "ollama"
    assert implementation["task_profile"]["intent"] == "implementation"
    assert implementation["fallback_chain"][0]["backend"] == "codex"


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
    assert [item["backend"] for item in chain] == ["pi", "codex", "cursor", "claude_code"]
    assert chain[0]["model"] == "tencent/hy3-preview"
    assert chain[-1]["backend"] == "claude_code"


def test_high_complexity_auto_uses_stronger_installed_local_not_configured_9b(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda settings: [
            {"id": "ornith:9b", "provider": "ollama", "local": True, "free": True},
            {"id": "ornith:35b", "provider": "ollama", "local": True, "free": True},
        ],
    )
    resolved = apply_auto_step_role_policy(
        {"backend": "pi", "model": "auto", "complexity": "high"},
        workflow=type(
            "Workflow",
            (),
            {"run_settings": '{"auto_route_models": true, "prefer_free_local": true}'},
        )(),
        config={"model_policy": {"mode": "auto", "prefer_local": True}},
        settings={"coding_llm_provider": "ollama", "coding_llm_model": "ornith:9b"},
        step_role="planning",
    )

    assert resolved["backend"] == "pi"
    assert resolved["model_provider"] == "ollama"
    assert resolved["model"] == "ornith:35b"


def test_auto_model_selection_reuses_run_scoped_catalog_cache(monkeypatch):
    from distr.core.project_cli_backends.model_policy import _free_eligible_model

    monkeypatch.setattr(
        "distr.core.project_cli_backends.models_catalog.pi_cli_models",
        lambda _settings: (_ for _ in ()).throw(AssertionError("catalog was reprobed")),
    )
    settings = {
        "coding_llm_provider": "ollama",
        "coding_llm_model": "ornith:9b",
        "_pi_cli_models_cache": [
            {"id": "ornith:9b", "provider": "ollama", "local": True, "free": True},
            {"id": "ornith:35b", "provider": "ollama", "local": True, "free": True},
        ],
    }

    selected = _free_eligible_model(settings, complexity="high", prefer_local=True)

    assert selected["model"] == "ornith:35b"


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
        ticket_title="Research example artist",
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


def test_one_shot_backend_enforces_configured_safety_ceiling(tmp_path):
    class SlowBackend(OneShotCliBackend):
        id = "slow_ceiling_test"
        name = "Slow ceiling test backend"

        def setup_status(self):
            return SimpleNamespace(ready=True, path="/bin/sh")

        def _build_command(self, executable, task):
            return [executable, "-c", "exec sleep 30"]

    task = ProjectTask(
        project_id=992,
        project_name="Safety ceiling fixture",
        folder=str(tmp_path),
        instruction="wait",
        board_id=20,
        adapter_options={"timeout_seconds": 1},
    )

    started_at = time.monotonic()
    result = asyncio.run(SlowBackend().send_task(task))

    assert time.monotonic() - started_at < 5
    assert result.success is False
    assert result.error == "Slow ceiling test backend reached its 1s safety ceiling and was stopped."
    assert (992, "slow_ceiling_test", 20) not in _ONE_SHOT_PROCESSES


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


def test_ticket_handoff_bullets_are_not_mislabeled_as_file_edits():
    message = _execution_event_message(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": (
                    "# DecisionsAI step handoff\n"
                    "- workflow: Development\n"
                    "- ticket: Preserve commerce\n"
                    "- project: Example Artist\n"
                ),
            },
        }
    )
    assert message == "Worker received the ticket and step context."


def test_command_output_with_bullets_is_not_mislabeled_as_file_edits():
    message = _execution_event_message(
        {
            "type": "message_update",
            "assistantMessageEvent": {
                "type": "text_delta",
                "delta": "exec\nrtk sed -n '1,20p' AGENTS.md\n- one\n- two\n- three",
            },
        }
    )
    assert message == "Worker ran a project command."


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
    assert _is_duplicate_progress_event(
        {"type": "message_update"},
        "Worker is checking another file.",
        previous_message=message,
        previous_at=10.0,
        now=10.7,
    )
    assert not _is_duplicate_progress_event(
        {"type": "message_update"},
        "Worker is checking another file.",
        previous_message=message,
        previous_at=10.0,
        now=11.1,
    )
    assert _is_duplicate_progress_event(
        {"type": "message_update"},
        message,
        previous_message=message,
        previous_at=10.0,
        now=14.9,
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
    assert command[:8] == [
        "/usr/local/bin/pi", "-p", "--mode", "json", "--provider", "ollama", "--model", "ornith:35b"
    ]
    system_prompt = command[command.index("--append-system-prompt") + 1]
    assert "Status: <choose exactly one: completed, failed, or needs_input>" in system_prompt
    assert "Do not copy the alternatives into Status" in system_prompt
    assert "Do not omit Status" in system_prompt
    assert command[-1] == "Scope the landing page."


def test_pi_workflow_command_requires_exact_expected_output_labels():
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder="/tmp/example-artist",
        instruction="Return a compact context packet.",
        origin="workflow",
        adapter_options={
            "expected_outputs": ["context_packet", "unknowns"],
        },
    )

    command = _pi_print_command("pi", task)
    system_prompt = command[command.index("--append-system-prompt") + 1]

    assert "context_packet: <value>" in system_prompt
    assert "unknowns: <value>" in system_prompt
    assert system_prompt.index("context_packet: <value>") < system_prompt.index("Status:")


def test_pi_preserves_opening_handoff_fields_and_terminal_contract_in_long_output(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    output = (
        "context_packet: Ticket and project loaded.\n"
        "unknowns: None.\n"
        + ("Detailed evidence that must be compacted. " * 500)
        + "\nStatus: completed\nSummary: Context handoff complete.\n"
        "Files changed: none\nCommands run: none\nBlockers: none"
    )
    final_event = json.dumps({
        "type": "message_end",
        "message": {"role": "assistant", "content": [{"type": "text", "text": output}]},
    })
    process = _FakeAsyncProcess([final_event])
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        lambda *_args, **_kwargs: _async_value(process),
    )
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder=str(tmp_path),
        instruction="Return the handoff.",
        origin="workflow",
        adapter_options={"expected_outputs": ["context_packet", "unknowns"]},
    )

    result = asyncio.run(PiBackend().send_task(task))

    assert result.success is True
    assert result.output.startswith("context_packet:")
    assert "unknowns: None." in result.output
    assert "[... omitted" in result.output
    assert "Status: completed" in result.output
    assert result.output.endswith("Blockers: none")


def test_pi_keeps_complete_report_when_model_adds_non_contract_epilogue(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    report = (
        "context_packet: Existing docs and project state reconciled.\n"
        "unknowns: Browser screenshots are still missing.\n"
        "Status: completed\n"
        "Summary: Context handoff complete.\n"
        "Files changed: none\nCommands run: none\nBlockers: none"
    )
    epilogue = "Context packet complete. Moving forward now."
    events = [
        json.dumps({
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": report}]},
        }),
        json.dumps({
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": epilogue}]},
        }),
    ]
    process = _FakeAsyncProcess(events)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        lambda *_args, **_kwargs: _async_value(process),
    )
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder=str(tmp_path),
        instruction="Return the handoff.",
        origin="workflow",
        adapter_options={"expected_outputs": ["context_packet", "unknowns"]},
    )

    result = asyncio.run(PiBackend().send_task(task))

    assert result.success is True
    assert "context_packet: Existing docs" in result.output
    assert "Status: completed" in result.output
    assert result.output.endswith(epilogue)


def test_pi_agent_end_finishes_complete_work_without_waiting_for_process_eof(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    report = (
        "execution_contract: Reuse the approved brief and evidence.\n"
        "dependency_status: None.\n"
        "Status: completed\n"
        "Summary: Contract confirmed.\n"
        "Files changed: none\nCommands run: none\nBlockers: none"
    )
    process = _TerminalEventProcess([
        json.dumps({
            "type": "message_end",
            "message": {"role": "assistant", "content": [{"type": "text", "text": report}]},
        }),
        json.dumps({"type": "agent_end"}),
    ])
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        lambda *_args, **_kwargs: _async_value(process),
    )
    task = ProjectTask(
        project_id=12,
        project_name="Acceptance fixture",
        folder=str(tmp_path),
        instruction="Confirm the contract.",
        origin="workflow",
        adapter_options={"expected_outputs": ["execution_contract", "dependency_status"]},
    )

    result = asyncio.run(asyncio.wait_for(PiBackend().send_task(task), timeout=0.5))

    assert result.success is True
    assert result.output.startswith("execution_contract:")
    assert process.returncode == 0


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


def test_pi_read_only_workflow_step_excludes_mutating_tools():
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder="/tmp/example-artist",
        instruction="Inspect the ticket and return a context packet.",
        origin="workflow",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        adapter_options={
            "model_provider": "openrouter",
            "read_only_expected": True,
            "step_role": "planning",
        },
    )

    command = _pi_print_command("pi", task)

    assert command[command.index("--tools") + 1] == "read,grep,find,ls"
    assert "bash" not in command
    assert "edit" not in command
    assert "write" not in command


def test_pi_tool_free_synthesis_step_disables_all_tools():
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder="/tmp/example-artist",
        instruction="Synthesize the supplied planning fields into a contract.",
        origin="workflow",
        adapter_options={
            "read_only_expected": True,
            "disable_tools": True,
            "step_role": "planning",
        },
    )

    command = _pi_print_command("pi", task)

    assert "--no-tools" in command
    assert "--tools" not in command


def test_pi_read_only_review_can_validate_public_sources_without_mutating_files():
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder="/tmp/example-artist",
        instruction="Validate the cited artist sources.",
        origin="workflow",
        adapter_options={"read_only_expected": True, "step_role": "review"},
    )

    command = _pi_print_command("pi", task)

    assert command[command.index("--tools") + 1] == "read,grep,find,ls,web_search,web_fetch"
    assert "bash" not in command
    assert "edit" not in command
    assert "write" not in command


def test_pi_error_prefers_provider_rate_limit_over_parser_noise():
    assert _preferred_model_error([
        "429 Rate limit exceeded: free-models-per-min.",
        "Separator is found, but chunk is longer than limit",
    ]) == "429 Rate limit exceeded: free-models-per-min."


def test_pi_empty_success_is_classified_as_failed_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    process = _FakeAsyncProcess([])
    subprocess_kwargs = {}

    def create_process(*_args, **kwargs):
        subprocess_kwargs.update(kwargs)
        return _async_value(process)

    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        create_process,
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
    assert subprocess_kwargs["limit"] == PI_JSONL_STREAM_LIMIT
    assert subprocess_kwargs["limit"] > 64 * 1024


def test_pi_stops_overbroad_inspection_at_the_configured_tool_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    tool_event = json.dumps({"type": "tool_execution_start", "toolName": "read"})
    process = _FakeAsyncProcess([tool_event, tool_event, tool_event, tool_event])
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        lambda *_args, **_kwargs: _async_value(process),
    )
    task = ProjectTask(
        project_id=12,
        project_name="Example Artist",
        folder=str(tmp_path),
        instruction="Inspect only the bounded ticket context.",
        origin="workflow",
        model="free/planner:free",
        adapter_options={
            "model_provider": "openrouter",
            "inspection_budget": {"max_tool_calls": 2},
        },
    )
    events: list[dict] = []

    result = asyncio.run(PiBackend().send_task(task, events.append))

    assert result.success is False
    assert "used 3 tool calls" in result.error
    assert process.returncode == -15
    assert any(event.get("type") == "inspection_budget_exceeded" for event in events)


def test_pi_soft_inspection_budget_allows_small_overage_to_finish(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )
    tool_event = json.dumps({"type": "tool_execution_start", "toolName": "read"})
    final_event = json.dumps({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Status: completed\nSummary: contract confirmed"}],
        },
    })
    process = _FakeAsyncProcess([tool_event, tool_event, tool_event, final_event])
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        lambda *_args, **_kwargs: _async_value(process),
    )
    task = ProjectTask(
        project_id=12,
        project_name="Acceptance fixture",
        folder=str(tmp_path),
        instruction="Confirm the bounded ticket contract.",
        origin="workflow",
        model="free/planner:free",
        adapter_options={
            "model_provider": "openrouter",
            "inspection_budget": {
                "max_tool_calls": 2,
                "hard_max_tool_calls": 4,
                "enforcement": "soft",
            },
        },
    )
    events: list[dict] = []

    result = asyncio.run(PiBackend().send_task(task, events.append))

    assert result.success is True
    assert process.returncode == 0
    assert any(event.get("type") == "inspection_budget_warning" for event in events)
    assert not any(event.get("type") == "inspection_budget_exceeded" for event in events)


def test_long_running_local_pi_worker_emits_heartbeat_without_blocking_event_loop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "distr.core.pi_rpc.PiRpcSession.find_pi",
        lambda: "/usr/bin/pi",
    )

    final_event = json.dumps({
        "type": "message_end",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Status: completed\nSummary: local work finished"}],
        },
    })
    process = _FakeAsyncProcess([final_event], first_line_delay=10.2)
    monkeypatch.setattr(
        "distr.core.project_cli_backends.registry.asyncio.create_subprocess_exec",
        lambda *_args, **_kwargs: _async_value(process),
    )
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


def test_pi_workflow_report_accepts_complete_named_handoff_for_deterministic_validation():
    output = """context_packet: Ticket and project loaded with docs/brief.md evidence.
unknowns: None.
route_recommendation: Use the local worker.
ui_design_read_if_applicable: Dark, tactile, music-first identity.
"""

    assert _pi_workflow_report_error(
        output,
        expected_outputs=[
            "context_packet",
            "unknowns",
            "route_recommendation",
            "ui_design_read_if_applicable",
        ],
    ) == ""


def test_pi_workflow_report_accepts_markdown_formatted_named_handoff():
    output = """## Context handoff
**context_packet:** Ticket and project loaded with docs/brief.md evidence.
- **unknowns:** None.
`route_recommendation`: Use the local worker.
### **ui_design_read_if_applicable:** Dark, tactile, music-first identity.
"""

    assert _pi_workflow_report_error(
        output,
        expected_outputs=[
            "context_packet",
            "unknowns",
            "route_recommendation",
            "ui_design_read_if_applicable",
        ],
    ) == ""


def test_pi_workflow_report_accepts_semicolon_delimited_named_handoff():
    output = (
        "Status: completed\n"
        "rerun_results: N/A because this research ticket made no code change; "
        "skip_or_blocker_reason: no ticket-scoped defect remains; "
        "next_action: proceed to the report step"
    )

    assert _pi_workflow_report_error(
        output,
        expected_outputs=["rerun_results", "skip_or_blocker_reason", "next_action"],
    ) == ""


def test_router_accepts_semantically_equivalent_standard_cli_review_headings():
    from distr.core.workflow.router import _missing_expected_outputs

    output = """Status: completed
Summary: Independently reviewed the ticket artifact; no ticket blockers found.
Tests run: N/A for this documentation-only ticket.
Security: Ticket scope is clean. One pre-existing project release finding remains elsewhere.
Browser evidence: docs/evidence/spotify.png and docs/evidence/youtube.png.
Visual claim verdicts: both screenshots visibly support the cited artist cues.
Files changed: none.
Blockers: none.
"""

    assert _missing_expected_outputs(
        output,
        [
            "review_findings",
            "project_release_findings",
            "check_results",
            "security_audit",
            "browser_evidence",
            "visual_claim_verdicts",
            "ship_verdict",
        ],
    ) == []


def test_pi_workflow_report_rejects_completed_status_without_named_handoff_fields():
    error = _pi_workflow_report_error(
        "Status: completed\nSummary: I verified the ticket.",
        expected_outputs=["execution_contract", "dependency_status"],
    )

    assert "omitted required workflow fields" in error
    assert "execution_contract" in error
    assert "dependency_status" in error


def test_pi_workflow_report_does_not_accept_incomplete_or_blocked_named_handoff():
    incomplete = "context_packet: loaded\nunknowns: None"
    blocked = """context_packet: loaded
unknowns: missing credentials
route_recommendation: local
ui_design_read_if_applicable: N/A
Blockers: missing credentials
"""

    expected = ["context_packet", "unknowns", "route_recommendation", "ui_design_read_if_applicable"]
    assert "required" in _pi_workflow_report_error(incomplete, expected_outputs=expected)
    assert "required" in _pi_workflow_report_error(blocked, expected_outputs=expected)


def test_step_auto_model_inherits_concrete_board_route():
    route = {"backend": "pi", "model": "ornith:35b", "source": "board_override"}
    merged = StepExecutorMixin._apply_step_harness_overrides(route, {"model": "auto"})
    assert merged["model"] == "ornith:35b"


def test_stored_coordination_route_is_not_reselected_by_runtime_auto_policy():
    assert not StepExecutorMixin._should_apply_runtime_auto_policy(
        approved_override={},
        stored_step_route={"backend": "codex", "model": "auto", "source": "run_coordination_plan"},
        scoped_route_enabled=False,
    )
    assert StepExecutorMixin._should_apply_runtime_auto_policy(
        approved_override={},
        stored_step_route={},
        scoped_route_enabled=False,
    )


def test_visual_review_capability_survives_provider_candidate_metadata_loss():
    capabilities = _route_required_capabilities(
        {"step_role": "review"},
        {"backend": "pi", "model": "candidate/free", "step_role": "review"},
        {
            "ticket_title": "Review supplied artist sources",
            "ticket_workflow_brief": "Browser evidence required: screenshots of Spotify and YouTube.",
        },
    )

    assert capabilities == ["tools", "vision"]


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


def test_dual_llm_judge_fails_closed_when_validator_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "distr.core.orchestrator_validator.run_orchestrator_validator_judgment",
        lambda **kwargs: None,
    )
    step = type("Step", (), {
        "id": 1,
        "validation_type": "llm_judgment",
        "validation_prompt": "The contract must match existing evidence.",
        "config": json.dumps({"review_mode": "dual"}),
    })()

    assert not _run_verification(
        step,
        "Status: completed\nA vague worker-authored contract.",
        True,
    )


def test_llm_judgment_uses_coordination_validator_routes(monkeypatch):
    calls = []

    def judge(**kwargs):
        calls.append(kwargs)
        return {"passed": True, "rationale": "PASS", "model": kwargs["route"]["model"]}

    monkeypatch.setattr(
        "distr.core.orchestrator_validator.run_orchestrator_validator_judgment",
        judge,
    )
    step = type("Step", (), {
        "id": 1,
        "validation_type": "llm_judgment",
        "validation_prompt": "The contract must match existing evidence.",
        "config": json.dumps({"review_mode": "dual"}),
    })()
    routes = [{"model_provider": "openrouter", "model": "validator/free"}]

    assert _run_verification(
        step,
        "Status: completed\nEvidence is exact.",
        True,
        validation_routes=routes,
    )
    assert calls[0]["route"] == routes[0]
    assert calls[0]["mode"] == "independent_primary"


def test_advisory_validator_outage_does_not_fail_a_completed_correction(monkeypatch):
    monkeypatch.setattr(
        "distr.core.orchestrator_validator.run_orchestrator_validator_judgment",
        lambda **_kwargs: None,
    )
    step = type("Step", (), {
        "id": 1,
        "name": "Correct defects found by validation",
        "validation_type": "llm_judgment",
        "validation_prompt": "Corrections are complete or explicitly not applicable.",
        "config": json.dumps({"step_role": "implementation"}),
    })()

    assert _run_verification(
        step,
        "Status: completed\nrerun_results: N/A\nskip_or_blocker_reason: no defect\nnext_action: report",
        True,
        validation_routes=[{"model_provider": "openrouter", "model": "offline-validator"}],
    )


def test_dual_review_validator_outage_still_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "distr.core.orchestrator_validator.run_orchestrator_validator_judgment",
        lambda **_kwargs: None,
    )
    step = type("Step", (), {
        "id": 1,
        "name": "Independently review and validate the change",
        "validation_type": "llm_judgment",
        "validation_prompt": "Review must independently pass.",
        "config": json.dumps({"step_role": "review", "review_mode": "dual"}),
    })()

    assert not _run_verification(
        step,
        "Status: completed\nship_verdict: pass",
        True,
        validation_routes=[{"model_provider": "openrouter", "model": "offline-validator"}],
    )


def test_required_browser_evidence_cannot_be_declared_visually_not_applicable(tmp_path):
    spotify = tmp_path / "spotify.png"
    youtube = tmp_path / "youtube.png"
    spotify.write_bytes(b"png")
    youtube.write_bytes(b"png")
    step = type("Step", (), {
        "id": 1,
        "name": "Independently review and validate the change",
        "validation_type": "llm_judgment",
        "validation_prompt": "The ticket evidence is genuinely validated.",
        "config": "{}",
    })()
    ticket = "Browser evidence required: screenshots of Spotify and YouTube."
    result = (
        "Status: completed\n"
        f"browser_evidence: {spotify}, {youtube}\n"
        "visual_claim_verdicts: N/A — documentation ticket, not UI\n"
        "Blockers: none"
    )

    assert not _run_verification(step, result, True, ticket_context=ticket)


def test_research_acceptance_evidence_does_not_bypass_explicit_dual_llm_validator(
    monkeypatch, tmp_path
):
    calls = []

    def judge(**kwargs):
        calls.append(kwargs)
        return {"passed": True, "rationale": "Evidence is coherent."}

    monkeypatch.setattr(
        "distr.core.orchestrator_validator.run_orchestrator_validator_judgment",
        judge,
    )
    screenshot = tmp_path / "source.png"
    screenshot.write_bytes(b"png")
    step = type("Step", (), {
        "id": 1,
        "name": "Independent review",
        "validation_type": "llm_judgment",
        "validation_prompt": "The research artifact and evidence satisfy the ticket.",
        "config": json.dumps({"review_mode": "dual"}),
    })()
    ticket = """
Research the artist and write a design direction document.
Non-goals: No code changes.
Browser evidence required: screenshot of the supplied source.
"""
    result = (
        "Status: completed\n"
        "Summary: Acceptance criteria and documentary deliverables verified.\n"
        f"browser_evidence: {screenshot}\n"
        "visual_claim_verdicts: PASS — inspected source artwork and recorded the visible palette.\n"
        "Files changed: docs/design-direction.md\n"
        "Blockers: none"
    )
    route = {"model_provider": "openrouter", "model": "validator/free"}

    assert _run_verification(
        step,
        result,
        True,
        ticket_context=ticket,
        validation_routes=[route],
    )
    assert calls and calls[0]["route"] == route


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


def test_runtime_provider_failover_inserts_codex_before_claude_for_partial_pi_chain():
    route = {
        "backend": "pi",
        "model": "vendor/text-only",
        "fallback_chain": [
            {"backend": "cursor", "model": "auto", "automatic": False},
            {"backend": "claude_code", "model": "auto", "automatic": True},
        ],
    }

    assert StepExecutorMixin._runtime_provider_fallback_route(route, {}) == {
        "backend": "codex",
        "model": "auto",
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


def test_step_auto_mode_ignores_stale_scoped_model_snapshot():
    config = {
        "model_policy": {"mode": "auto", "free_only": True},
        "execution_route": {
            "enabled": True,
            "mode": "scoped",
            "scoped_model_key": "ornith:35b",
            "route_snapshot": {
                "backend_id": "pi",
                "provider": "ollama",
                "model": "ornith:35b",
                "name": "Ornith 35B",
            },
        },
    }

    route = StepExecutorMixin._apply_step_harness_overrides(
        {"backend": "codex", "model": "auto", "source": "auto_policy"},
        config,
    )

    assert StepExecutorMixin._step_execution_route_enabled(config) is False
    assert route["backend"] == "codex"
    assert route["model"] == "auto"
    assert route["source"] == "auto_policy"


def test_step_scoped_dropdown_remains_the_pin_when_auto_is_off():
    config = {
        "model_policy": {"mode": "manual"},
        "execution_route": {
            "enabled": True,
            "mode": "scoped",
            "scoped_model_key": "ornith:35b",
            "route_snapshot": {
                "backend_id": "pi",
                "provider": "ollama",
                "model": "ornith:35b",
                "name": "Ornith 35B",
            },
        },
    }

    route = StepExecutorMixin._apply_step_harness_overrides(
        {"backend": "codex", "model": "auto"},
        config,
    )

    assert StepExecutorMixin._step_execution_route_enabled(config) is True
    assert route["backend"] == "pi"
    assert route["model_provider"] == "ollama"
    assert route["model"] == "ornith:35b"


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


def test_pi_reasoning_payload_is_never_exposed_as_execution_message():
    from distr.core.project_cli_backends.registry import _execution_event_message

    event = {
        "type": "turn_end",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "private chain of thought",
                    "thinkingSignature": "opaque-provider-signature",
                }
            ],
        },
    }

    message = _execution_event_message(event)

    assert message == "Worker is reasoning over the ticket and project evidence."
    assert "thinkingSignature" not in message
    assert "private chain" not in message


def test_pi_execution_event_is_bounded_before_live_delivery():
    from distr.core.project_cli_backends.registry import _compact_execution_event

    event = {
        "type": "message_update",
        "assistantMessageEvent": {
            "type": "text_delta",
            "delta": "credential-fragment-that-must-not-be-durable",
            "partial": {"content": "x" * 200_000},
        },
        "message": {
            "role": "assistant",
            "content": "y" * 20_000,
            "thinkingSignature": "private-signature",
        },
    }

    compact = _compact_execution_event(event)

    assert compact["type"] == "message_update"
    assert compact["update_type"] == "text_delta"
    assert "credential-fragment" not in str(compact)
    assert "partial" not in str(compact)
    assert "thinkingSignature" not in str(compact)
