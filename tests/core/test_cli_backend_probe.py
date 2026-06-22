from __future__ import annotations

from unittest.mock import patch

from distr.core.project_cli_backends.catalog_probe import (
    backend_truth_contract,
    backend_capability_contract,
    backend_probe_commands,
    codex_models,
    probe_cli_backend,
)
from distr.core.project_cli_backends.base import BackendStatus


def test_codex_capability_contract_is_explicit():
    items = backend_capability_contract("codex")
    ids = [item["id"] for item in items]
    assert ids == ["model", "reasoning_effort", "service_tier"]
    assert items[1]["values"] == ["low", "medium", "high"]
    assert items[2]["values"] == ["default", "flex", "fast"]


def test_codex_probe_commands_include_models():
    commands = backend_probe_commands("codex", "/usr/local/bin/codex")
    assert commands[0] == ["/usr/local/bin/codex", "models"]
    assert ["codex", "models"] != commands[0]


def test_codex_models_falls_back_to_auto_when_unverified():
    with patch("distr.core.project_cli_backends.catalog_probe.subprocess.run", side_effect=RuntimeError("boom")):
        models, source, message = codex_models({})
    assert source == "codex-unverified"
    assert models[0]["id"] == "auto"
    assert "Use Auto for Codex-managed model selection" in message


def test_probe_cli_backend_surfaces_setup_message():
    fake_status = BackendStatus(
        id="codex",
        name="Codex CLI",
        installed=False,
        ready=False,
        state="missing",
        message="Codex CLI is not installed.",
        setup_required=True,
        setup_instructions="Install and authenticate Codex CLI.",
    )
    fake_model_result = {
        "models": [{"id": "auto", "name": "Auto", "provider": "codex", "backend_id": "codex"}],
        "source": "codex-unverified",
        "message": "Codex CLI did not return a verified model list.",
        "kind": "cli",
        "supports_model_picker": True,
    }
    with patch("distr.core.project_cli_backends.catalog_probe.get_backend") as get_backend_mock:
        get_backend_mock.return_value.name = "Codex CLI"
        get_backend_mock.return_value.setup_status.return_value = fake_status
        with patch("distr.core.project_cli_backends.catalog_probe.models_for_cli_backend", return_value=fake_model_result):
            with patch("distr.core.project_cli_backends.catalog_probe.run_probe_command", return_value={"argv": ["codex", "models"], "ok": False, "stderr": "missing"}):
                report = probe_cli_backend("codex", {})
    assert report["next_step"] == "Codex CLI is not installed."
    assert report["status"]["setup_instructions"] == "Install and authenticate Codex CLI."


def test_backend_truth_contract_marks_codex_unverified_as_not_workflow_ready():
    status = BackendStatus(
        id="codex",
        name="Codex CLI",
        installed=True,
        ready=True,
        state="ready",
        message="Codex CLI is installed and ready.",
    )
    truth = backend_truth_contract(status, {
        "models": [{"id": "auto", "name": "Auto", "provider": "codex"}],
        "source": "codex-unverified",
        "message": "Codex CLI did not return a verified model list.",
        "kind": "cli",
        "supports_model_picker": True,
    })
    assert truth["workflow_ready"] is False
    assert truth["health_state"] == "setup"
    assert truth["catalog_verified"] is False


def test_backend_truth_contract_marks_verified_catalog_as_ready():
    status = BackendStatus(
        id="kiro",
        name="Kiro CLI",
        installed=True,
        ready=True,
        state="ready",
        message="Kiro CLI is installed and ready.",
    )
    truth = backend_truth_contract(status, {
        "models": [{"id": "claude-sonnet-4-5", "name": "Claude Sonnet 4.5", "provider": "kiro"}],
        "source": "kiro-cli",
        "message": "",
        "kind": "cli",
        "supports_model_picker": True,
    })
    assert truth["workflow_ready"] is True
    assert truth["health_state"] == "ready"
    assert truth["catalog_verified"] is True
