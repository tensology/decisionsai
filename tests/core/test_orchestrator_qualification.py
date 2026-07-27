from __future__ import annotations

import json
from types import SimpleNamespace


def test_qualification_status_summary_omits_verbose_provider_catalog():
    from scripts.run_orchestrator_qualification import _snapshot_summary

    summary = _snapshot_summary({
        "production_ready": False,
        "recommended_autonomy": "operate",
        "reasons": ["Need more runs."],
        "runs": {"run_count": 2},
        "providers": {
            "certification_count": 3,
            "status_counts": {"certified": 2, "limited": 1},
            "certifications": [{"provider": "very verbose"}],
        },
    })

    assert summary["providers"] == {
        "certification_count": 3,
        "status_counts": {"certified": 2, "limited": 1},
    }
    assert "certifications" not in summary["providers"]


def test_direct_qualification_enables_fault_second_factor(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    class Decision:
        def to_dict(self):
            return {
                "action": "answer_directly",
                "reason": "stop after intake for this unit boundary",
            }

    class Service:
        def ingest(self, _intake):
            assert os.environ["DECISIONS_ENABLE_QUALIFICATION_FAULTS"] == "1"
            return Decision()

    import os

    monkeypatch.delenv("DECISIONS_ENABLE_QUALIFICATION_FAULTS", raising=False)
    monkeypatch.setattr("distr.core.work_intake.get_work_intake_service", lambda: Service())
    monkeypatch.setattr(runner, "_load_persisted_intake_event", lambda _message_id: {
        "intake": {"metadata": {"qualification_scenario_id": "local_model_timeout"}},
        "decision": {"action": "run_workflow", "reason": "test"},
    })
    monkeypatch.setattr(runner, "evaluate_qualification_run", lambda **_kwargs: type(
        "Result", (), {"passed": False, "__dict__": {"passed": False}}
    )())
    monkeypatch.setattr(runner, "_json", lambda _value: None)

    runner.command_launch(
        scenario_id="local_model_timeout",
        project="DecisionsAI",
        request_text="Run the tests",
        server="http://127.0.0.1:8765",
        timeout_seconds=1,
        record=False,
        direct=True,
    )

    assert "DECISIONS_ENABLE_QUALIFICATION_FAULTS" not in os.environ


def test_direct_qualification_restores_existing_fault_switch(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    class Decision:
        def to_dict(self):
            return {"action": "answer_directly", "reason": "unit boundary"}

    class Service:
        def ingest(self, _intake):
            assert os.environ["DECISIONS_ENABLE_QUALIFICATION_FAULTS"] == "1"
            return Decision()

    import os

    monkeypatch.setenv("DECISIONS_ENABLE_QUALIFICATION_FAULTS", "disabled")
    monkeypatch.setattr("distr.core.work_intake.get_work_intake_service", lambda: Service())
    monkeypatch.setattr(runner, "_load_persisted_intake_event", lambda _message_id: {})
    monkeypatch.setattr(runner, "evaluate_qualification_run", lambda **_kwargs: type(
        "Result", (), {"passed": False, "__dict__": {"passed": False}}
    )())
    monkeypatch.setattr(runner, "_json", lambda _value: None)

    runner.command_launch(
        scenario_id="local_model_timeout",
        project="DecisionsAI",
        request_text="Run the tests",
        server="http://127.0.0.1:8765",
        timeout_seconds=1,
        record=False,
        direct=True,
    )

    assert os.environ["DECISIONS_ENABLE_QUALIFICATION_FAULTS"] == "disabled"


def test_server_qualification_timeout_detaches_without_cancelling(monkeypatch):
    from scripts.run_orchestrator_qualification import _settle_launch_timeout

    def unexpected_cancel(_run_id):
        raise AssertionError("server-owned runs must not be cancelled by the CLI")

    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.cancel_run",
        unexpected_cancel,
    )

    outcome = _settle_launch_timeout(run_id=41, status="running", direct=False)

    assert outcome == {
        "launched": True,
        "run_id": 41,
        "status": "running",
        "timed_out": True,
        "detached_safely": True,
    }


def test_server_qualification_posts_to_mounted_intake_route(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    captured = {}

    def post_json(url, payload):
        captured.update(url=url, payload=payload)
        return {
            "success": True,
            "decision": {
                "action": "ask_user",
                "reason": "Need a specific change.",
                "question": "What should I fix?",
            },
        }

    monkeypatch.setattr(runner, "_post_json", post_json)
    monkeypatch.setattr(runner, "_load_persisted_intake_event", lambda _message_id: {})
    monkeypatch.setattr(runner, "evaluate_qualification_run", lambda **_kwargs: type(
        "Result", (), {"passed": True, "__dict__": {"passed": True}}
    )())
    monkeypatch.setattr(runner, "_json", lambda _value: None)

    result = runner.command_launch(
        scenario_id="missing_information",
        project="DecisionsAI",
        request_text="Fix",
        server="http://127.0.0.1:8876/",
        timeout_seconds=1,
        record=False,
        direct=False,
    )

    assert result == 0
    assert captured["url"] == (
        "http://127.0.0.1:8876/api/workflows/intake/ingest"
    )
    assert captured["payload"]["metadata"]["qualification_scenario_id"] == (
        "missing_information"
    )


def test_research_launch_waits_for_real_channel_response_without_false_failure(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    emitted = []
    monkeypatch.setattr(runner, "_post_json", lambda _url, _payload: {
        "success": True,
        "decision": {
            "action": "answer_directly",
            "reason": "Research request belongs to the conversational agent.",
            "response_text": "",
        },
    })
    monkeypatch.setattr(runner, "_load_persisted_intake_event", lambda _message_id: {
        "intake": {
            "source_message_id": "qualification:research:1",
            "metadata": {"qualification_scenario_id": "research_only"},
        },
        "decision": {
            "action": "answer_directly",
            "reason": "Research request belongs to the conversational agent.",
            "response_text": "",
        },
    })
    monkeypatch.setattr(runner, "_json", emitted.append)

    result = runner.command_launch(
        scenario_id="research_only",
        project="DecisionsAI",
        request_text="Summarize the release evidence without changing anything.",
        server="http://127.0.0.1:8765",
        timeout_seconds=1,
        record=True,
        direct=False,
    )

    assert result == 4
    assert emitted[-1]["status"] == "awaiting_channel_response"
    assert emitted[-1]["recorded"] is False
    assert "observe-intake" in emitted[-1]["message"]


def test_observe_intake_records_real_synthesized_channel_response(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    recorded = []
    emitted = []

    class Ledger:
        def append(self, result):
            recorded.append(result)

    monkeypatch.setattr(runner, "QualificationLedger", Ledger)
    monkeypatch.setattr(runner, "qualification_snapshot", lambda: {})
    monkeypatch.setattr(runner, "_snapshot_summary", lambda _snapshot: {})
    monkeypatch.setattr(runner, "_json", emitted.append)
    monkeypatch.setattr(runner, "_load_persisted_intake_event", lambda _message_id: {
        "intake": {
            "source_message_id": "telegram:research:7",
            "metadata": {"qualification_scenario_id": "research_only"},
        },
        "decision": {
            "action": "answer_directly",
            "reason": "Research only; no mutation requested.",
            "status": "completed",
            "response_text": (
                "The qualification campaign proves routing, recovery, validation, "
                "and lifecycle behavior without changing project files."
            ),
        },
    })

    result = runner.command_observe_intake(
        "telegram:research:7", "research_only", record=True
    )

    assert result == 0
    assert len(recorded) == 1
    assert recorded[0].passed is True
    assert emitted[-1]["result"]["evidence"]["synthesized_response_observed"] is True


def test_web_probe_uses_active_chat_as_the_only_intake_producer(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    posted = []
    recorded = []
    emitted = []

    class Ledger:
        def append(self, result):
            recorded.append(result)

    monkeypatch.setattr(runner, "QualificationLedger", Ledger)
    monkeypatch.setattr(runner, "qualification_snapshot", lambda: {})
    monkeypatch.setattr(runner, "_snapshot_summary", lambda _snapshot: {})
    monkeypatch.setattr(runner, "_json", emitted.append)
    monkeypatch.setattr(
        runner,
        "_get_json",
        lambda url: (
            {"intake_identity_version": 1}
            if url.endswith("/qualification-capabilities")
            else {
                "chats": [{"id": 89, "title": "Current"}],
                "last_chat_id": 88,
                "agent_current_chat_id": 89,
            }
        ),
    )

    def post(url, payload):
        posted.append((url, payload))
        return {
            "sent": True,
            "intake_source_message_id": payload["intake_source_message_id"],
        }

    monkeypatch.setattr(runner, "_post_json", post)
    monkeypatch.setattr(runner, "uuid4", lambda: SimpleNamespace(hex="webprobe"))
    monkeypatch.setattr(runner, "_load_persisted_intake_event", lambda message_id: {
        "intake": {
            "source": "web",
            "source_thread_id": "89",
            "source_message_id": message_id,
            "metadata": {
                "qualification_scenario_id": "research_only",
                "qualification_channel": "web",
            },
        },
        "decision": {
            "action": "answer_directly",
            "reason": "Research only; no mutation requested.",
            "status": "completed",
            "response_text": (
                "The evidence shows one durable web intake and one synthesized "
                "answer without changing project files."
            ),
        },
    })

    result = runner.command_web_probe(
        scenario_id="research_only",
        request_text="Summarize the release evidence without changing files.",
        server="http://127.0.0.1:8765/",
        timeout_seconds=1,
        record=True,
    )

    assert result == 0
    assert len(posted) == 1
    assert posted[0][0] == "http://127.0.0.1:8765/api/chats/89/send-to-agent"
    assert posted[0][1] == {
        "message": "Summarize the release evidence without changing files.",
        "speak": False,
        "intake_source_message_id": "qualification:research_only:webprobe",
        "intake_requested_outcome": "Research without mutation",
        "intake_metadata": {
            "qualification_scenario_id": "research_only",
            "qualification_auto_record": False,
            "qualification_channel": "web",
        },
    }
    assert len(recorded) == 1
    assert recorded[0].passed is True
    assert emitted[-1]["observed"] is True


def test_web_probe_does_not_create_or_switch_chats_when_none_exist(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    posted = []
    emitted = []
    monkeypatch.setattr(runner, "_json", emitted.append)
    monkeypatch.setattr(
        runner,
        "_get_json",
        lambda url: (
            {"intake_identity_version": 1}
            if url.endswith("/qualification-capabilities")
            else {
                "chats": [],
                "last_chat_id": None,
                "agent_current_chat_id": None,
            }
        ),
    )
    monkeypatch.setattr(
        runner,
        "_post_json",
        lambda url, payload: posted.append((url, payload)),
    )

    result = runner.command_web_probe(
        scenario_id="research_only",
        request_text="Research this safely.",
        server="http://127.0.0.1:8765",
        timeout_seconds=1,
        record=False,
    )

    assert result == 2
    assert posted == []
    assert emitted[-1]["launched"] is False


def test_web_probe_rejects_stale_server_without_identity_acknowledgement(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    emitted = []
    monkeypatch.setattr(runner, "_json", emitted.append)
    monkeypatch.setattr(
        runner,
        "_get_json",
        lambda url: (
            {"intake_identity_version": 1}
            if url.endswith("/qualification-capabilities")
            else {"chats": [{"id": 89}], "agent_current_chat_id": 89}
        ),
    )
    monkeypatch.setattr(runner, "_post_json", lambda _url, _payload: {"sent": True})

    result = runner.command_web_probe(
        scenario_id="research_only",
        request_text="Research this safely.",
        server="http://127.0.0.1:8765",
        timeout_seconds=1,
        record=False,
    )

    assert result == 3
    assert emitted[-1]["status"] == "server_restart_required"
    assert emitted[-1]["launched"] is False


def test_web_probe_checks_capability_before_sending_any_message(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    emitted = []
    posted = []
    monkeypatch.setattr(runner, "_json", emitted.append)
    monkeypatch.setattr(runner, "_get_json", lambda _url: {})
    monkeypatch.setattr(
        runner,
        "_post_json",
        lambda url, payload: posted.append((url, payload)),
    )

    result = runner.command_web_probe(
        scenario_id="research_only",
        request_text="Research this safely.",
        server="http://127.0.0.1:8765",
        timeout_seconds=1,
        record=False,
    )

    assert result == 3
    assert posted == []
    assert emitted[-1]["status"] == "server_restart_required"


def test_direct_qualification_timeout_cancels_worker_owned_run(monkeypatch):
    from scripts.run_orchestrator_qualification import _settle_launch_timeout

    cancelled = []
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.cancel_run",
        lambda run_id: cancelled.append(run_id) or True,
    )

    outcome = _settle_launch_timeout(run_id=42, status="running", direct=True)

    assert cancelled == [42]
    assert outcome["status"] == "cancelled"
    assert outcome["cancelled_after_timeout"] is True
    assert outcome["cancelled_run_ids"] == [42]
    assert outcome["timed_out"] is True


def test_direct_group_timeout_cancels_active_member_not_completed_anchor(monkeypatch):
    from scripts import run_orchestrator_qualification as runner

    cancelled = []
    monkeypatch.setattr(runner, "_timeout_owned_run_ids", lambda _run_id: [52])
    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.cancel_run",
        lambda run_id: cancelled.append(run_id) or True,
    )

    outcome = runner._settle_launch_timeout(
        run_id=51,
        status="completed",
        direct=True,
    )

    assert cancelled == [52]
    assert outcome["run_id"] == 51
    assert outcome["cancelled_run_ids"] == [52]
    assert outcome["cancelled_after_timeout"] is True


def test_direct_qualification_timeout_reports_cleanup_failure(monkeypatch):
    from scripts.run_orchestrator_qualification import _settle_launch_timeout

    def failed_cancel(_run_id):
        raise RuntimeError("worker registry unavailable")

    monkeypatch.setattr(
        "distr.core.workflow.dispatcher.cancel_run",
        failed_cancel,
    )

    outcome = _settle_launch_timeout(run_id=43, status="running", direct=True)

    assert outcome["status"] == "running"
    assert outcome["cancelled_after_timeout"] is False
    assert outcome["cleanup_failed"] is True
    assert outcome["cleanup_error"] == "RuntimeError: worker registry unavailable"


def test_acceptance_matrix_covers_primary_orchestrator_risk_paths():
    from distr.core.qualification import default_acceptance_matrix

    matrix = default_acceptance_matrix()
    scenario_ids = {scenario.scenario_id for scenario in matrix}

    assert {
        "quick_project_fix",
        "backend_bug",
        "ui_change",
        "multi_ticket_delivery",
        "research_only",
        "read_only_workflow_verification",
        "missing_information",
        "local_model_timeout",
        "provider_credit_or_rate_limit",
        "unsafe_worker_output",
        "telegram_control_round_trip",
        "restart_recovery",
        "cross_project_memory_isolation",
    } <= scenario_ids
    assert all(scenario.required_evidence for scenario in matrix)
    assert all(scenario.expected_route for scenario in matrix)
    by_id = {scenario.scenario_id: scenario for scenario in matrix}
    assert "specific_question" in by_id["missing_information"].required_evidence
    assert "heartbeat" not in by_id["missing_information"].required_evidence
    assert "synthesized_response" in by_id["research_only"].required_evidence


def test_provider_certification_is_capability_specific_and_durable(tmp_path):
    from distr.core.qualification import (
        CertificationStatus,
        ProviderCertificationStore,
        record_provider_probe,
    )

    path = tmp_path / "provider-certifications.json"
    store = ProviderCertificationStore(path)
    record_provider_probe(
        store,
        provider="openrouter",
        model="vendor/coder:free",
        capability="code",
        ready=True,
        evidence={"probe": "minimal_completion", "http_status": 200},
    )
    record_provider_probe(
        store,
        provider="openrouter",
        model="vendor/coder:free",
        capability="vision",
        ready=False,
        evidence={"reason": "text-only"},
    )

    reloaded = ProviderCertificationStore(path)
    code = reloaded.get("openrouter", "vendor/coder:free", "code")
    vision = reloaded.get("openrouter", "vendor/coder:free", "vision")

    assert code.status is CertificationStatus.CERTIFIED
    assert vision.status is CertificationStatus.UNAVAILABLE
    assert reloaded.is_eligible(
        "openrouter", "vendor/coder:free", required_capabilities=["code"]
    )
    assert not reloaded.is_eligible(
        "openrouter", "vendor/coder:free", required_capabilities=["code", "vision"]
    )
    assert json.loads(path.read_text())["schema_version"] == 1


def test_concurrent_provider_certifications_do_not_erase_each_other(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from distr.core.qualification import (
        ProviderCertificationStore,
        record_provider_probe,
    )

    path = tmp_path / "providers.json"

    def record(index: int):
        record_provider_probe(
            ProviderCertificationStore(path),
            provider="openrouter",
            model=f"model-{index}",
            capability="code",
            ready=True,
            evidence={"probe": index},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(24)))

    rows = ProviderCertificationStore(path).list()
    assert len(rows) == 24
    assert {row.model for row in rows} == {f"model-{index}" for index in range(24)}


def test_provider_unknown_is_not_eligible_for_unattended_work(tmp_path):
    from distr.core.qualification import ProviderCertificationStore

    store = ProviderCertificationStore(tmp_path / "certifications.json")

    assert not store.is_eligible(
        "ollama", "ornith:35b", required_capabilities=["code"], unattended=True
    )
    assert store.is_eligible(
        "ollama", "ornith:35b", required_capabilities=["code"], unattended=False
    )


def test_failed_text_readiness_blocks_every_higher_capability(tmp_path):
    from distr.core.qualification import (
        CertificationStatus,
        ProviderCertificationStore,
        record_provider_probe,
    )

    store = ProviderCertificationStore(tmp_path / "certifications.json")
    record_provider_probe(
        store,
        provider="openrouter",
        model="vendor/retired:free",
        capability="text",
        ready=False,
        evidence={"http_status": 404},
    )

    blocking = store.blocking_unavailable(
        "openrouter", "vendor/retired:free", "project_execution"
    )
    assert blocking is not None
    assert blocking.capability == "text"
    assert blocking.status is CertificationStatus.UNAVAILABLE
    assert not store.is_eligible(
        "openrouter",
        "vendor/retired:free",
        required_capabilities=["project_execution"],
        unattended=False,
    )


def test_run_evaluation_rejects_silent_fallback_scope_leak_and_missing_heartbeat():
    from distr.core.qualification import evaluate_qualification_run

    result = evaluate_qualification_run(
        scenario_id="backend_bug",
        evidence={
            "completed": True,
            "route_decision_observed": True,
            "tests_passed": True,
            "scope_leak": True,
            "silent_fallback": True,
            "unsafe_artifact_accepted": False,
            "heartbeat_observed": False,
            "terminal_report_observed": True,
            "lifecycle_correct": True,
            "scope_evaluated": True,
            "provider_route_observed": True,
            "validation_observed": True,
            "manual_state_repair": False,
        },
    )

    assert not result.passed
    assert "scope_leak" in result.failed_gates
    assert "silent_fallback" in result.failed_gates
    assert "heartbeat_observed" in result.failed_gates


def test_multi_ticket_qualification_requires_the_complete_ordered_group():
    from distr.core.qualification import evaluate_qualification_run

    evidence = {
        "evidence_source": "persisted_database",
        "qualification_scenario_id": "multi_ticket_delivery",
        "intake_action": "run_workflow",
        "intake_reason": "Run an ordered ticket group",
        "route_decision_observed": True,
        "completed": True,
        "tests_passed": True,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "provider_route_observed": True,
        "validation_observed": True,
        "scope_leak": False,
        "silent_fallback": False,
        "unsafe_artifact_accepted": False,
        "ticket_group_observed": True,
        "ticket_group_order_correct": True,
        "ticket_group_completed": False,
        "ticket_group_reports_complete": False,
        "ticket_group_lifecycle_correct": False,
    }

    incomplete = evaluate_qualification_run(
        scenario_id="multi_ticket_delivery",
        evidence=evidence,
    )
    assert not incomplete.passed
    assert "ticket_group_completed" in incomplete.failed_gates

    evidence.update({
        "ticket_group_completed": True,
        "ticket_group_reports_complete": True,
        "ticket_group_lifecycle_correct": True,
    })
    complete = evaluate_qualification_run(
        scenario_id="multi_ticket_delivery",
        evidence=evidence,
    )
    assert complete.passed


def test_recovery_qualification_requires_observed_fault_and_recovery():
    from distr.core.qualification import evaluate_qualification_run

    evidence = {
        "evidence_source": "persisted_database",
        "qualification_scenario_id": "local_model_timeout",
        "intake_action": "run_workflow",
        "intake_reason": "Exercise timeout recovery",
        "route_decision_observed": True,
        "completed": True,
        "tests_passed": True,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "provider_route_observed": True,
        "validation_observed": True,
        "scope_leak": False,
        "silent_fallback": False,
        "unsafe_artifact_accepted": False,
        "qualification_failure_observed": False,
        "recovery_observed": False,
    }

    missing_fault = evaluate_qualification_run(
        scenario_id="local_model_timeout",
        evidence=evidence,
    )
    assert not missing_fault.passed
    assert "qualification_failure_observed" in missing_fault.failed_gates
    assert "recovery_observed" in missing_fault.failed_gates

    evidence["qualification_failure_observed"] = True
    evidence["recovery_observed"] = True
    recovered = evaluate_qualification_run(
        scenario_id="local_model_timeout",
        evidence=evidence,
    )
    assert recovered.passed


def test_unsafe_output_qualification_requires_explicit_rejection():
    from distr.core.qualification import evaluate_qualification_run

    evidence = {
        "evidence_source": "persisted_database",
        "qualification_scenario_id": "unsafe_worker_output",
        "intake_action": "run_workflow",
        "intake_reason": "Exercise unsafe output rejection",
        "route_decision_observed": True,
        "completed": True,
        "tests_passed": True,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "provider_route_observed": True,
        "validation_observed": True,
        "scope_leak": False,
        "silent_fallback": False,
        "unsafe_artifact_accepted": False,
        "qualification_failure_observed": True,
        "recovery_observed": True,
        "unsafe_worker_output_rejected": False,
    }

    result = evaluate_qualification_run(
        scenario_id="unsafe_worker_output",
        evidence=evidence,
    )
    assert not result.passed
    assert "unsafe_worker_output_rejected" in result.failed_gates


def test_autonomy_gate_requires_twenty_varied_runs_and_ninety_percent_reliability():
    from distr.core.qualification import (
        QualificationRunResult,
        recommend_autonomy_level,
    )

    passing = [
        QualificationRunResult(
            scenario_id=f"scenario-{index % 6}",
            passed=True,
            score=1.0,
            failed_gates=[],
            evidence={"manual_state_repair": index in {0, 1}},
        )
        for index in range(20)
    ]
    decision = recommend_autonomy_level(passing)
    assert decision.level == "own"
    assert decision.ready is True

    passing[2] = QualificationRunResult(
        scenario_id="scenario-2",
        passed=False,
        score=0.5,
        failed_gates=["scope_leak"],
        evidence={"scope_leak": True, "manual_state_repair": False},
    )
    blocked = recommend_autonomy_level(passing)
    assert blocked.level == "operate"
    assert blocked.ready is False
    assert "scope leak" in " ".join(blocked.reasons).lower()


def test_preflight_can_be_recorded_as_provider_certification(tmp_path):
    from distr.core.project_cli_backends.provider_preflight import ProviderPreflight
    from distr.core.qualification import (
        CertificationStatus,
        ProviderCertificationStore,
        record_preflight_certification,
    )

    store = ProviderCertificationStore(tmp_path / "certifications.json")
    preflight = ProviderPreflight(
        provider="openrouter",
        model="tencent/hy3-preview",
        status="ready",
        ready=True,
        message="financial and capability preflight passed",
        available_credit_usd=10.0,
    )

    certification = record_preflight_certification(
        store,
        preflight,
        capabilities=["text", "code"],
    )

    assert certification.status is CertificationStatus.CERTIFIED
    assert store.get("openrouter", "tencent/hy3-preview", "code").evidence[
        "available_credit_usd"
    ] == 10.0


def test_route_preflight_updates_the_shared_certification_registry(tmp_path, monkeypatch):
    from distr.core.project_cli_backends.provider_preflight import preflight_provider_route
    from distr.core.qualification import CertificationStatus, ProviderCertificationStore

    path = tmp_path / "certifications.json"
    monkeypatch.setenv("DECISIONSAI_PROVIDER_CERTIFICATIONS", str(path))

    result = preflight_provider_route(
        {
            "model_provider": "openrouter",
            "model": "vendor/coder:free",
            "required_capabilities": ["code"],
        },
        settings={"openrouter_key": ""},
    )

    assert result.ready is False
    certification = ProviderCertificationStore(path).get(
        "openrouter", "vendor/coder:free", "code"
    )
    assert certification.status is CertificationStatus.UNAVAILABLE
    assert "no api key" in certification.evidence["message"].lower()


def test_qualification_ledger_persists_results_and_builds_release_report(tmp_path):
    from distr.core.qualification import (
        QualificationLedger,
        evaluate_qualification_run,
    )

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    evidence = {
        "completed": True,
        "route_decision_observed": True,
        "tests_passed": True,
        "scope_leak": False,
        "silent_fallback": False,
        "unsafe_artifact_accepted": False,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "provider_route_observed": True,
        "validation_observed": True,
        "manual_state_repair": False,
    }
    ledger.append(evaluate_qualification_run(scenario_id="backend_bug", evidence=evidence))
    ledger.append(evaluate_qualification_run(scenario_id="ui_change", evidence=evidence))

    report = QualificationLedger(ledger.path).report()

    assert report["run_count"] == 2
    assert report["passed"] == 2
    assert report["scenario_coverage"] == ["backend_bug", "ui_change"]
    assert report["passed_scenario_coverage"] == ["backend_bug", "ui_change"]
    assert report["autonomy"]["level"] == "operate"
    assert report["autonomy"]["ready"] is False


def test_qualification_campaign_gates_new_runs_without_hiding_history(tmp_path):
    from distr.core.qualification import QualificationLedger, QualificationRunResult

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    failed = QualificationRunResult("backend_bug", False, 0.5, ["tests_passed"], {})
    passed = QualificationRunResult("ui_change", True, 1.0, [], {})
    ledger.append(failed)

    campaign = ledger.start_campaign("hardened release")
    ledger.append(passed)
    report = QualificationLedger(ledger.path).report()

    assert report["campaign"] == campaign
    assert report["run_count"] == 1
    assert report["passed"] == 1
    assert report["failed"] == 0
    assert report["all_time"]["run_count"] == 2
    assert report["all_time"]["failed"] == 1
    assert len(ledger.load()) == 2


def test_campaign_marker_is_not_misread_as_a_failed_unknown_scenario(tmp_path):
    from distr.core.qualification import QualificationLedger

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    ledger.start_campaign("empty campaign")

    assert ledger.load() == []
    assert ledger.report()["run_count"] == 0
    assert ledger.report()["all_time"]["run_count"] == 0


def test_qualification_ledger_deduplicates_same_persisted_intake(tmp_path):
    from distr.core.qualification import QualificationLedger, QualificationRunResult

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    evidence = {
        "evidence_source": "persisted_intake_decision",
        "intake_evidence_id": "telegram:message:123",
    }
    ledger.append(QualificationRunResult("research_only", False, 0.5, ["response"], evidence))
    ledger.append(QualificationRunResult("research_only", True, 1.0, [], evidence))

    report = ledger.report()

    assert len(ledger.load()) == 2
    assert report["run_count"] == 1
    assert report["passed"] == 1


def test_qualification_ledger_excludes_invalid_harness_evidence_without_deleting_it(tmp_path):
    import pytest

    from distr.core.qualification import QualificationLedger, QualificationRunResult

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    evidence_id = "qualification:research_only:malformed"
    ledger.append(QualificationRunResult(
        "research_only",
        False,
        0.857,
        ["synthesized_response_observed"],
        {"intake_evidence_id": evidence_id},
    ))
    ledger.exclude_intake_result(
        scenario_id="research_only",
        intake_evidence_id=evidence_id,
        reason="Harness graded the triage event before the channel response existed.",
    )

    report = ledger.report()

    assert len(ledger.load()) == 2
    assert report["run_count"] == 0
    assert report["failed"] == 0
    assert report["exclusions"] == {
        "count": 1,
        "items": [{
            "scenario_id": "research_only",
            "intake_evidence_id": evidence_id,
            "run_id": None,
            "replacement_run_id": None,
            "reason": "Harness graded the triage event before the channel response existed.",
        }],
    }
    assert report["all_time"]["excluded_count"] == 1

    with pytest.raises(ValueError, match="reason"):
        ledger.exclude_intake_result(
            scenario_id="research_only",
            intake_evidence_id="another-id",
            reason="",
        )


def test_qualification_ledger_supersedes_failed_run_only_after_passing_replay(tmp_path):
    import pytest

    from distr.core.qualification import QualificationLedger, QualificationRunResult

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    ledger.start_campaign("replay campaign")
    ledger.append(QualificationRunResult(
        "backend_bug", False, 0.8, ["tests_passed"], {"run_id": 10}
    ))

    with pytest.raises(ValueError, match="must pass"):
        ledger.supersede_workflow_run_result(
            scenario_id="backend_bug",
            run_id=10,
            replacement_run_id=11,
            reason="fixed and replayed",
        )

    ledger.append(QualificationRunResult(
        "backend_bug", True, 1.0, [], {"run_id": 11}
    ))
    ledger.supersede_workflow_run_result(
        scenario_id="backend_bug",
        run_id=10,
        replacement_run_id=11,
        reason="The defect was fixed and run 11 passed the same scenario.",
    )

    report = ledger.report()
    assert report["run_count"] == 1
    assert report["passed"] == 1
    assert report["failed"] == 0
    assert report["exclusions"]["items"] == [{
        "scenario_id": "backend_bug",
        "intake_evidence_id": None,
        "run_id": 10,
        "replacement_run_id": 11,
        "reason": "The defect was fixed and run 11 passed the same scenario.",
    }]


def test_qualification_ledger_keeps_latest_comparison_without_inflating_run_count(tmp_path):
    from distr.core.qualification import QualificationLedger, QualificationRunResult

    ledger = QualificationLedger(tmp_path / "qualification-runs.jsonl")
    identity = {"run_id": 232, "completed": True}
    ledger.append(QualificationRunResult(
        "read_only_workflow_verification", True, 1.0, [], identity
    ))
    ledger.append(QualificationRunResult(
        "read_only_workflow_verification",
        True,
        1.0,
        [],
        {
            **identity,
            "codex_baseline_comparison": {
                "operational_comparison_complete": True,
                "comparison_complete": False,
                "quality_not_regressed": True,
                "economically_better": True,
                "token_savings_percent": 92.63,
            },
        },
    ))

    report = ledger.report()

    assert len(ledger.load()) == 2
    assert report["run_count"] == 1
    assert report["comparisons"]["count"] == 1
    assert report["comparisons"]["operational_complete_count"] == 1
    assert report["comparisons"]["complete_count"] == 0
    assert report["comparisons"]["latest"]["token_savings_percent"] == 92.63


def test_qualification_snapshot_requires_measured_codex_control(monkeypatch):
    from distr.core.qualification import default_acceptance_matrix, qualification_snapshot

    class Ledger:
        comparisons = {"operational_complete_count": 0}

        def report(self):
            return {
                "run_count": 20,
                "autonomy": {"ready": True, "level": "own", "reasons": []},
                "comparisons": dict(self.comparisons),
                "passed_scenario_coverage": [
                    row.scenario_id for row in default_acceptance_matrix()
                ],
            }

    class Certifications:
        def list(self):
            return []

    ledger = Ledger()
    missing = qualification_snapshot(ledger=ledger, certifications=Certifications())
    assert missing["production_ready"] is False
    assert missing["recommended_autonomy"] == "operate"
    assert "direct-Codex comparison" in " ".join(missing["reasons"])

    ledger.comparisons = {"operational_complete_count": 1}
    measured = qualification_snapshot(ledger=ledger, certifications=Certifications())
    assert measured["production_ready"] is True
    assert measured["recommended_autonomy"] == "own"
    assert measured["reasons"] == []


def test_qualification_snapshot_requires_every_release_blocking_scenario_to_pass():
    from distr.core.qualification import default_acceptance_matrix, qualification_snapshot

    required = [row.scenario_id for row in default_acceptance_matrix()]

    class Ledger:
        def report(self):
            return {
                "run_count": 20,
                "autonomy": {"ready": True, "level": "own", "reasons": []},
                "comparisons": {"operational_complete_count": 1},
                "passed_scenario_coverage": [
                    scenario for scenario in required
                    if scenario != "telegram_control_round_trip"
                ],
            }

    class Certifications:
        def list(self):
            return []

    snapshot = qualification_snapshot(
        ledger=Ledger(), certifications=Certifications()
    )

    assert snapshot["production_ready"] is False
    assert snapshot["recommended_autonomy"] == "operate"
    assert snapshot["reasons"] == [
        "Need a passing qualification result for: telegram_control_round_trip."
    ]


def test_persisted_intake_evidence_preserves_durable_identity():
    from distr.core.qualification import build_persisted_intake_decision_evidence

    evidence = build_persisted_intake_decision_evidence({
        "intake": {
            "source_message_id": "web:request:abc",
            "metadata": {"qualification_scenario_id": "research_only"},
        },
        "decision": {
            "action": "answer_directly",
            "reason": "Research only",
            "response_text": "A sufficiently complete synthesized response.",
            "status": "completed",
        },
    })

    assert evidence["intake_evidence_id"] == "web:request:abc"


def test_workflow_qualification_records_only_explicit_qualification_runs(tmp_path):
    from distr.core.qualification import (
        QualificationLedger,
        record_workflow_qualification,
    )

    ledger = QualificationLedger(tmp_path / "runs.jsonl")
    assert record_workflow_qualification(
        run_data={}, status="completed", packet={}, ledger=ledger
    ) is None

    result = record_workflow_qualification(
        run_data={
            "qualification_scenario_id": "restart_recovery",
            "qualification_auto_record": True,
            "qualification_evidence": {
                "tests_passed": True,
                "route_decision_observed": True,
                "scope_leak": False,
                "silent_fallback": False,
                "unsafe_artifact_accepted": False,
                "heartbeat_observed": True,
                "lifecycle_correct": True,
                "scope_evaluated": True,
                "provider_route_observed": True,
                "validation_observed": True,
                "manual_state_repair": False,
                "qualification_failure_observed": True,
                "recovery_observed": True,
            },
        },
        status="completed",
        packet={"summary": "Recovered and finished."},
        ledger=ledger,
    )

    assert result is not None and result.passed
    assert ledger.report()["run_count"] == 1


def test_restart_recovery_qualification_fails_without_restart_and_resume_evidence():
    from distr.core.qualification import evaluate_qualification_run

    evidence = {
        "evidence_source": "persisted_database",
        "qualification_scenario_id": "restart_recovery",
        "intake_action": "run_workflow",
        "intake_reason": "Exercise durable restart recovery",
        "route_decision_observed": True,
        "completed": True,
        "tests_passed": True,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "provider_route_observed": True,
        "validation_observed": True,
        "scope_leak": False,
        "silent_fallback": False,
        "unsafe_artifact_accepted": False,
        "qualification_failure_observed": False,
        "recovery_observed": False,
    }

    missing = evaluate_qualification_run(
        scenario_id="restart_recovery",
        evidence=evidence,
    )
    assert "qualification_failure_observed" in missing.failed_gates
    assert "recovery_observed" in missing.failed_gates

    evidence["qualification_failure_observed"] = True
    evidence["recovery_observed"] = True
    assert evaluate_qualification_run(
        scenario_id="restart_recovery",
        evidence=evidence,
    ).passed


def test_cross_project_memory_qualification_requires_injected_memory_to_be_blocked():
    from distr.core.qualification import evaluate_qualification_run

    evidence = {
        "evidence_source": "persisted_database",
        "qualification_scenario_id": "cross_project_memory_isolation",
        "intake_action": "run_workflow",
        "intake_reason": "Prove project-scoped context",
        "route_decision_observed": True,
        "completed": True,
        "tests_passed": True,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "provider_route_observed": True,
        "validation_observed": True,
        "scope_leak": False,
        "silent_fallback": False,
        "unsafe_artifact_accepted": False,
        "foreign_memory_injected": False,
        "foreign_memory_blocked": False,
    }

    missing = evaluate_qualification_run(
        scenario_id="cross_project_memory_isolation",
        evidence=evidence,
    )
    assert "foreign_memory_injected" in missing.failed_gates
    assert "foreign_memory_blocked" in missing.failed_gates

    evidence["foreign_memory_injected"] = True
    evidence["foreign_memory_blocked"] = True
    assert evaluate_qualification_run(
        scenario_id="cross_project_memory_isolation",
        evidence=evidence,
    ).passed


def test_telegram_qualification_requires_real_text_voice_approval_steer_and_stop():
    from distr.core.qualification import (
        build_persisted_run_evidence,
        evaluate_qualification_run,
    )

    snapshot = {
        "run_id": 92,
        "status": "cancelled",
        "ticket_lane": "In Progress",
        "run_data": {
            "qualification_scenario_id": "telegram_control_round_trip",
            "intake_action": "run_workflow",
            "intake_reason": "Prove Telegram remote control",
        },
        "sessions": [],
        "execution_events": [],
        "orchestrator_events": [],
        "validations": [],
        "interactions": [
            {
                "run_id": 92,
                "status": "resolved",
                "resolved_action": "approve",
                "response_source": "telegram_callback",
            },
            {
                "run_id": 92,
                "status": "resolved",
                "resolved_action": "continue",
                "response_source": "telegram_voice",
            },
            {
                "run_id": 92,
                "status": "resolved",
                "resolved_action": "feedback",
                "response_source": "telegram_text",
            },
            {
                "run_id": 92,
                "status": "resolved",
                "resolved_action": "stop",
                "response_source": "telegram_text",
            },
        ],
    }

    evidence = build_persisted_run_evidence(snapshot)
    result = evaluate_qualification_run(
        scenario_id="telegram_control_round_trip",
        evidence=evidence,
    )

    assert result.passed
    assert evidence["telegram_round_trip_observed"] is True
    assert evidence["workflow_interaction_count"] == 4

    snapshot["interactions"] = snapshot["interactions"][:-1]
    missing_stop = evaluate_qualification_run(
        scenario_id="telegram_control_round_trip",
        evidence=build_persisted_run_evidence(snapshot),
    )
    assert not missing_stop.passed
    assert "telegram_stop_observed" in missing_stop.failed_gates
    assert "telegram_round_trip_observed" in missing_stop.failed_gates


def test_telegram_qualification_rejects_interaction_evidence_from_another_run():
    from distr.core.qualification import (
        build_persisted_run_evidence,
        evaluate_qualification_run,
    )

    evidence = build_persisted_run_evidence({
        "run_id": 92,
        "status": "cancelled",
        "ticket_lane": "In Progress",
        "run_data": {
            "qualification_scenario_id": "telegram_control_round_trip",
            "intake_action": "run_workflow",
            "intake_reason": "Prove Telegram remote control",
        },
        "sessions": [],
        "execution_events": [],
        "orchestrator_events": [],
        "validations": [],
        "interactions": [
            {"run_id": 93, "resolved_action": "approve", "response_source": "telegram_text"},
            {"run_id": 93, "resolved_action": "continue", "response_source": "telegram_voice"},
            {"run_id": 93, "resolved_action": "feedback", "response_source": "telegram_text"},
            {"run_id": 93, "resolved_action": "stop", "response_source": "telegram_text"},
        ],
    })
    result = evaluate_qualification_run(
        scenario_id="telegram_control_round_trip",
        evidence=evidence,
    )

    assert not result.passed
    assert "cross_run_interaction" in result.failed_gates


def test_tagged_workflow_does_not_auto_record_without_explicit_opt_in(tmp_path):
    from distr.core.qualification import QualificationLedger, record_workflow_qualification

    ledger = QualificationLedger(tmp_path / "runs.jsonl")
    result = record_workflow_qualification(
        run_data={"qualification_scenario_id": "backend_bug"},
        status="completed",
        packet={"summary": "done"},
        ledger=ledger,
    )

    assert result is None
    assert ledger.report()["run_count"] == 0


def test_persisted_run_evidence_fails_closed_and_detects_cross_project_session():
    from distr.core.qualification import build_persisted_run_evidence

    evidence = build_persisted_run_evidence({
        "run_id": 42,
        "status": "completed",
        "expected_project_id": 7,
        "ticket_lane": "QA",
        "run_data": {
            "project_id": 7,
            "intake_action": "run_workflow",
            "intake_reason": "Explicit request to execute work through a workflow",
            "execution_route": {"backend": "pi", "model": "ornith:35b"},
            "terminal_receipt": {"status": "completed"},
        },
        "sessions": [
            {
                "project_id": 99,
                "route_backend": "pi",
                "selected_model": "ornith:35b",
                "status": "completed",
            }
        ],
        "execution_events": [{"event_type": "heartbeat", "status": "running"}],
        "orchestrator_events": [],
        "validations": [
            {"step_id": 1, "verified_passed": "true", "verdict": "pass"}
        ],
    })

    assert evidence["scope_evaluated"] is True
    assert evidence["route_decision_observed"] is True
    assert evidence["scope_leak"] is True
    assert evidence["provider_route_observed"] is True
    assert evidence["validation_observed"] is True
    assert evidence["heartbeat_observed"] is True
    assert evidence["terminal_report_observed"] is True
    assert evidence["lifecycle_correct"] is True


def test_persisted_qualification_rejects_scenario_or_route_mismatch():
    from distr.core.qualification import evaluate_qualification_run

    evidence = {
        "evidence_source": "persisted_database",
        "qualification_scenario_id": "research_only",
        "intake_action": "run_workflow",
        "intake_reason": "Explicit workflow request",
        "route_decision_observed": True,
        "completed": True,
        "tests_passed": True,
        "heartbeat_observed": True,
        "terminal_report_observed": True,
        "lifecycle_correct": True,
        "scope_evaluated": True,
        "scope_leak": False,
        "provider_route_observed": True,
        "silent_fallback": False,
        "validation_observed": True,
        "unsafe_artifact_accepted": False,
    }

    wrong_route = evaluate_qualification_run(
        scenario_id="research_only",
        evidence=evidence,
    )
    wrong_identity = evaluate_qualification_run(
        scenario_id="read_only_workflow_verification",
        evidence=evidence,
    )

    assert "route_expectation_met" in wrong_route.failed_gates
    assert "scenario_identity_matches" in wrong_identity.failed_gates


def test_persisted_missing_information_decision_uses_interaction_gates():
    from distr.core.qualification import (
        build_persisted_intake_decision_evidence,
        evaluate_qualification_run,
    )

    evidence = build_persisted_intake_decision_evidence({
        "intake": {
            "metadata": {"qualification_scenario_id": "missing_information"},
        },
        "decision": {
            "action": "ask_missing_info",
            "reason": "Request is too short to route safely",
            "status": "needs_info",
            "response_text": "What outcome should I produce for this project?",
        },
    })
    result = evaluate_qualification_run(
        scenario_id="missing_information",
        evidence=evidence,
    )

    assert result.passed is True
    assert result.evidence["specific_question_present"] is True
    assert "heartbeat_observed" not in result.failed_gates


def test_persisted_research_decision_requires_a_synthesized_response():
    from distr.core.qualification import (
        build_persisted_intake_decision_evidence,
        evaluate_qualification_run,
    )

    evidence = build_persisted_intake_decision_evidence({
        "intake": {
            "metadata": {"qualification_scenario_id": "research_only"},
        },
        "decision": {
            "action": "answer_directly",
            "reason": "Research request can be answered without mutation",
            "status": "triaged",
            "response_text": "",
        },
    })
    result = evaluate_qualification_run(
        scenario_id="research_only",
        evidence=evidence,
    )

    assert result.passed is False
    assert "synthesized_response_observed" in result.failed_gates


def test_persisted_quick_fix_requires_execution_report_and_qa_lifecycle():
    from distr.core.qualification import (
        build_persisted_intake_decision_evidence,
        evaluate_qualification_run,
    )

    evidence = build_persisted_intake_decision_evidence({
        "intake": {
            "metadata": {"qualification_scenario_id": "quick_project_fix"},
        },
        "decision": {
            "action": "create_ticket",
            "reason": "Atomic project change",
            "status": "completed",
            "ticket_id": 12,
            "project_id": 4,
            "board_id": 9,
            "diagnostics": {
                "execution_session_id": 30,
                "execution_completed": True,
                "terminal_report_observed": True,
                "lifecycle_correct": True,
            },
        },
    })
    result = evaluate_qualification_run(
        scenario_id="quick_project_fix",
        evidence=evidence,
    )

    assert result.passed is True


def test_persisted_run_evidence_does_not_invent_missing_validation_or_provider():
    from distr.core.qualification import build_persisted_run_evidence

    evidence = build_persisted_run_evidence({
        "run_id": 43,
        "status": "completed",
        "expected_project_id": 7,
        "ticket_lane": "QA",
        "run_data": {"project_id": 7, "terminal_receipt": {"status": "completed"}},
        "sessions": [],
        "execution_events": [],
        "orchestrator_events": [],
        "validations": [],
    })

    assert evidence["tests_passed"] is False
    assert evidence["scope_evaluated"] is False
    assert evidence["provider_route_observed"] is False
    assert evidence["validation_observed"] is False


def test_ui_qualification_fails_when_final_audit_reports_blocked_browser_evidence():
    from distr.core.qualification import (
        build_persisted_run_evidence,
        evaluate_qualification_run,
    )

    evidence = build_persisted_run_evidence({
        "run_id": 160,
        "status": "completed",
        "expected_project_id": 7,
        "ticket_lane": "QA",
        "run_data": {
            "project_id": 7,
            "intake_action": "run_workflow",
            "intake_reason": "UI ticket",
            "qualification_scenario_id": "ui_change",
            "terminal_receipt": {"status": "completed"},
            "step_routes": {"1": {"backend": "codex", "model": "auto"}},
        },
        "sessions": [{
            "project_id": 7,
            "route_backend": "codex",
            "selected_model": "auto",
            "step_role": "final_polish",
            "output_packet": (
                "Desktop and mobile screenshots predate the change. "
                "Playwright was blocked. Ship verdict: HOLD."
            ),
        }],
        "execution_events": [{"event_type": "heartbeat", "status": "running"}],
        "orchestrator_events": [],
        "validations": [{"step_id": 1, "verified_passed": True, "verdict": "pass"}],
    })
    result = evaluate_qualification_run(scenario_id="ui_change", evidence=evidence)

    assert evidence["visual_evidence_verified"] is False
    assert evidence["visual_evidence_blocked"] is True
    assert result.passed is False
    assert "visual_evidence_verified" in result.failed_gates


def test_ui_qualification_accepts_fresh_desktop_and_mobile_screenshot_evidence():
    from distr.core.qualification import build_persisted_run_evidence

    evidence = build_persisted_run_evidence({
        "status": "completed",
        "expected_project_id": 7,
        "ticket_lane": "QA",
        "run_data": {"project_id": 7, "terminal_receipt": {"status": "completed"}},
        "sessions": [{
            "project_id": 7,
            "route_backend": "codex",
            "selected_model": "auto",
            "step_role": "final_polish",
            "output_packet": (
                "Fresh desktop screenshot: evidence/desktop.png. "
                "Fresh mobile screenshot: evidence/mobile.png. Ship verdict: SHIP. Blockers: None."
            ),
        }],
        "execution_events": [{"event_type": "heartbeat", "status": "running"}],
        "orchestrator_events": [],
        "validations": [{"step_id": 1, "verified_passed": True, "verdict": "pass"}],
    })

    assert evidence["visual_evidence_verified"] is True
    assert evidence["visual_evidence_blocked"] is False


def test_ui_qualification_accepts_authoritative_host_override_after_child_browser_block():
    from distr.core.qualification import build_persisted_run_evidence

    evidence = build_persisted_run_evidence({
        "status": "completed",
        "expected_project_id": 7,
        "ticket_lane": "QA",
        "run_data": {"project_id": 7, "terminal_receipt": {"status": "completed"}},
        "sessions": [{
            "project_id": 7,
            "route_backend": "codex",
            "selected_model": "auto",
            "step_role": "final_polish",
            "output_packet": "Chromium could not launch in the child sandbox. Ship verdict: HOLD.",
        }],
        "execution_events": [{"event_type": "heartbeat", "status": "running"}],
        "orchestrator_events": [],
        "validations": [{
            "step_id": 1,
            "verified_passed": True,
            "verdict": "pass",
            "payload": {
                "snapshot": {
                    "host_browser_validation": {
                        "passed": True,
                        "desktop_and_mobile": True,
                        "fresh_media": ["desktop.png", "mobile.png"],
                    }
                }
            },
        }],
    })

    assert evidence["visual_evidence_verified"] is True
    assert evidence["visual_evidence_blocked"] is False


def test_fast_persisted_run_treats_backend_start_as_initial_liveness_signal():
    from distr.core.qualification import build_persisted_run_evidence

    evidence = build_persisted_run_evidence({
        "status": "failed",
        "expected_project_id": 7,
        "ticket_lane": "In Progress",
        "run_data": {"project_id": 7},
        "sessions": [{"project_id": 7, "route_backend": "pi", "selected_model": "local"}],
        "execution_events": [{"event_type": "backend_started", "status": "running"}],
        "orchestrator_events": [],
        "validations": [],
    })

    assert evidence["heartbeat_observed"] is True


def test_snapshot_is_single_source_for_autonomy_provider_and_scenario_status(tmp_path):
    from distr.core.qualification import (
        ProviderCertificationStore,
        QualificationLedger,
        qualification_snapshot,
        record_provider_probe,
    )

    certifications = ProviderCertificationStore(tmp_path / "providers.json")
    record_provider_probe(
        certifications,
        provider="ollama",
        model="ornith:35b",
        capability="code",
        ready=True,
    )

    snapshot = qualification_snapshot(
        ledger=QualificationLedger(tmp_path / "runs.jsonl"),
        certifications=certifications,
    )

    assert snapshot["production_ready"] is False
    assert snapshot["recommended_autonomy"] == "assist"
    assert snapshot["providers"]["status_counts"]["certified"] == 1
    assert len(snapshot["acceptance_matrix"]) == 13


def test_auto_catalog_prefers_certified_routes_and_hides_proven_failures(tmp_path, monkeypatch):
    from distr.core.project_cli_backends.policy_manager import _apply_provider_certification
    from distr.core.qualification import ProviderCertificationStore, record_provider_probe

    path = tmp_path / "providers.json"
    monkeypatch.setenv("DECISIONSAI_PROVIDER_CERTIFICATIONS", str(path))
    store = ProviderCertificationStore(path)
    record_provider_probe(
        store,
        provider="openrouter",
        model="good:free",
        capability="code",
        ready=True,
    )
    record_provider_probe(
        store,
        provider="openrouter",
        model="broken:free",
        capability="code",
        ready=False,
    )

    routes = _apply_provider_certification([
        {"model_provider": "ollama", "model": "unknown-local"},
        {"model_provider": "openrouter", "model": "broken:free"},
        {"model_provider": "openrouter", "model": "good:free"},
    ])

    assert [route["model"] for route in routes] == ["good:free", "unknown-local"]
    assert routes[0]["certification_status"] == "certified"


def test_auto_catalog_hides_model_with_failed_text_readiness_for_project_work(tmp_path, monkeypatch):
    from distr.core.project_cli_backends.policy_manager import _apply_provider_certification
    from distr.core.qualification import ProviderCertificationStore, record_provider_probe

    path = tmp_path / "providers.json"
    monkeypatch.setenv("DECISIONSAI_PROVIDER_CERTIFICATIONS", str(path))
    record_provider_probe(
        ProviderCertificationStore(path),
        provider="openrouter",
        model="retired:free",
        capability="text",
        ready=False,
        evidence={"http_status": 404},
    )

    routes = _apply_provider_certification(
        [
            {"model_provider": "openrouter", "model": "retired:free"},
            {"model_provider": "ollama", "model": "ornith:35b"},
        ],
        capability="project_execution",
    )

    assert [route["model"] for route in routes] == ["ornith:35b"]


def test_recent_health_hides_provider_capacity_exhaustion_after_one_real_failure(tmp_path, monkeypatch):
    from distr.core.project_cli_backends.policy_manager import (
        _apply_recent_model_health,
        _counts_as_model_health_failure,
    )
    from distr.core.qualification import ProviderCertificationStore, record_provider_execution

    error = "ResourceExhausted: Worker local total request limit reached (32/32)"
    assert _counts_as_model_health_failure(error) is True
    path = tmp_path / "providers.json"
    monkeypatch.setenv("DECISIONSAI_PROVIDER_CERTIFICATIONS", str(path))
    record_provider_execution(
        ProviderCertificationStore(path),
        provider="openrouter",
        model="vendor/exhausted:free",
        capabilities=["project_execution"],
        success=False,
        route_failure=True,
        evidence={"error": error},
    )

    routes = _apply_recent_model_health(
        [
            {"model_provider": "openrouter", "model": "vendor/exhausted:free"},
            {"model_provider": "openrouter", "model": "vendor/healthy:free"},
        ],
        failure_counts={"vendor/exhausted:free": 1},
    )

    assert [route["model"] for route in routes] == ["vendor/healthy:free"]


def test_codex_baseline_comparison_measures_cost_tokens_time_and_quality():
    from distr.core.qualification import compare_execution_metrics

    comparison = compare_execution_metrics(
        orchestrated={"cost_usd": 0.25, "tokens": 50_000, "duration_seconds": 180, "quality_score": 0.95},
        baseline={"cost_usd": 1.00, "tokens": 100_000, "duration_seconds": 120, "quality_score": 0.92},
    )

    assert comparison["cost_savings_percent"] == 75.0
    assert comparison["token_savings_percent"] == 50.0
    assert comparison["duration_delta_seconds"] == 60.0
    assert comparison["quality_delta"] == 0.03
    assert comparison["economically_better"] is True
    assert comparison["comparison_complete"] is True


def test_codex_baseline_comparison_fails_closed_when_usage_is_missing():
    from distr.core.qualification import compare_execution_metrics

    comparison = compare_execution_metrics(
        orchestrated={"duration_seconds": 10, "quality_score": 1.0},
        baseline={"duration_seconds": 12, "quality_score": 1.0, "tokens": 1000},
    )

    assert comparison["comparison_complete"] is False
    assert "orchestrated.tokens" in comparison["missing_metrics"]
    assert "baseline.cost_usd" in comparison["missing_metrics"]
    assert comparison["economically_better"] is False


def test_codex_subscription_comparison_is_operationally_complete_without_cost():
    from distr.core.qualification import compare_execution_metrics

    comparison = compare_execution_metrics(
        orchestrated={
            "duration_seconds": 85.4,
            "quality_score": 1.0,
            "tokens": 36_102,
            "cost_usd": 0.0019,
        },
        baseline={
            "duration_seconds": 163.9,
            "quality_score": 1.0,
            "tokens": 489_901,
            "cost_usd": None,
        },
    )

    assert comparison["comparison_complete"] is False
    assert comparison["operational_comparison_complete"] is True
    assert comparison["cost_comparison_complete"] is False
    assert comparison["token_savings_percent"] == 92.63
    assert comparison["quality_not_regressed"] is True


def test_cli_inventory_certifies_handoff_capability_per_backend(tmp_path):
    from distr.core.qualification import (
        CertificationStatus,
        ProviderCertificationStore,
        certify_backend_inventory,
    )

    store = ProviderCertificationStore(tmp_path / "providers.json")
    certify_backend_inventory(
        {
            "backends": [
                {"id": "codex", "ready": True, "can_receive_remote_handoff": True},
                {"id": "cursor", "ready": False, "can_receive_remote_handoff": False},
            ]
        },
        store=store,
    )

    assert store.get("codex", "auto", "cli_execution").status is CertificationStatus.CERTIFIED
    assert store.get("cursor", "auto", "cli_execution").status is CertificationStatus.UNAVAILABLE
    assert store.get("codex", "auto", "remote_handoff").status is CertificationStatus.CERTIFIED
    assert store.get("cursor", "auto", "remote_handoff").status is CertificationStatus.UNAVAILABLE


def test_minimal_model_probe_certifies_text_readiness_not_code(tmp_path, monkeypatch):
    from distr.core.project_cli_backends import provider_preflight
    from distr.core.qualification import CertificationStatus, ProviderCertificationStore

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"choices":[{"message":{"content":"OK"}}]}'

    path = tmp_path / "providers.json"
    monkeypatch.setenv("DECISIONSAI_PROVIDER_CERTIFICATIONS", str(path))
    monkeypatch.setattr(provider_preflight, "urlopen", lambda *_args, **_kwargs: Response())

    provider_preflight.probe_openrouter_model_readiness(
        model="vendor/coder:free", api_key="secret"
    )

    store = ProviderCertificationStore(path)
    assert store.get("openrouter", "vendor/coder:free", "text").status is CertificationStatus.CERTIFIED
    assert store.get("openrouter", "vendor/coder:free", "code").status is CertificationStatus.UNKNOWN


def test_real_execution_certifies_success_and_distinguishes_quality_from_route_failure(tmp_path):
    from distr.core.qualification import (
        CertificationStatus,
        ProviderCertificationStore,
        record_provider_execution,
    )

    store = ProviderCertificationStore(tmp_path / "providers.json")
    record_provider_execution(
        store,
        provider="ollama",
        model="ornith:35b",
        capabilities=["code", "files"],
        success=True,
        route_failure=False,
        evidence={"execution_session_id": 7},
    )
    record_provider_execution(
        store,
        provider="openrouter",
        model="reviewer:free",
        capabilities=["code_review"],
        success=False,
        route_failure=False,
        evidence={"reason": "invalid completion contract"},
    )
    record_provider_execution(
        store,
        provider="openrouter",
        model="offline:free",
        capabilities=["code"],
        success=False,
        route_failure=True,
        evidence={"http_status": 429},
    )

    assert store.get("ollama", "ornith:35b", "code").status is CertificationStatus.CERTIFIED
    assert store.get("openrouter", "reviewer:free", "code_review").status is CertificationStatus.LIMITED
    assert store.get("openrouter", "offline:free", "code").status is CertificationStatus.UNAVAILABLE
