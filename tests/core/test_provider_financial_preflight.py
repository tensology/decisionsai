import asyncio
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

from distr.core.project_cli_backends.provider_preflight import (
    preflight_provider_route,
    probe_openrouter_model_readiness,
    rank_openrouter_free_models,
)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def _route(model="tencent/hy3-preview"):
    return {"backend": "pi", "model_provider": "openrouter", "model": model}


def _openrouter_responses(*, account_remaining, key_remaining):
    def respond(request, **_kwargs):
        if request.full_url.endswith("/credits"):
            return _Response({"data": {"total_credits": 10, "total_usage": 10 - account_remaining}})
        return _Response({"data": {"limit_remaining": key_remaining}})

    return respond


def test_openrouter_preflight_blocks_before_dispatch_when_credit_is_too_low(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        _openrouter_responses(account_remaining=0.03, key_remaining=None),
    )

    report = preflight_provider_route(
        _route(), settings={"openrouter_key": "secret"}, complexity="medium"
    )

    assert report.ready is False
    assert report.status == "blocked"
    assert report.available_credit_usd == 0.03
    assert report.required_buffer_usd == 0.10
    assert "secret" not in report.message


def test_openrouter_preflight_scales_safety_buffer_with_work_size(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        _openrouter_responses(account_remaining=0.20, key_remaining=None),
    )

    small = preflight_provider_route(_route(), settings={"openrouter_key": "k"}, complexity="low")
    collection = preflight_provider_route(_route(), settings={"openrouter_key": "k"}, complexity="high")

    assert small.ready is True
    assert collection.ready is False
    assert collection.required_buffer_usd == 0.50


def test_free_openrouter_route_only_blocks_negative_balance(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        _openrouter_responses(account_remaining=0, key_remaining=None),
    )

    report = preflight_provider_route(
        _route("openrouter/free"), settings={"openrouter_key": "k"}, complexity="high"
    )

    assert report.ready is True
    assert report.required_buffer_usd == 0


def test_openrouter_402_is_a_blocking_preflight(monkeypatch):
    def denied(*_args, **_kwargs):
        raise HTTPError("https://openrouter.ai/api/v1/key", 402, "Payment Required", {}, io.BytesIO())

    monkeypatch.setattr("distr.core.project_cli_backends.provider_preflight.urlopen", denied)

    report = preflight_provider_route(_route(), settings={"openrouter_key": "k"})

    assert report.ready is False
    assert report.http_status == 402
    assert "insufficient credit" in report.message.lower()


def test_openrouter_account_balance_blocks_even_when_key_is_unlimited(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        _openrouter_responses(account_remaining=-0.43, key_remaining=None),
    )

    report = preflight_provider_route(
        _route(), settings={"openrouter_key": "k"}, complexity="high"
    )

    assert report.ready is False
    assert report.available_credit_usd == -0.43
    assert "$-0.43" in report.message


def test_unreachable_credit_probe_is_unverified_not_false_insufficient_credit(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )

    report = preflight_provider_route(_route(), settings={"openrouter_key": "k"})

    assert report.ready is None
    assert report.status == "unverified"


def test_provider_preflight_interaction_uses_proceed_and_stop_actions():
    from distr.core.workflow.interactions import allowed_actions_for_kind

    assert allowed_actions_for_kind("provider_preflight") == ["approve", "stop"]


def test_direct_project_prompt_is_not_dispatched_when_provider_has_no_credit(monkeypatch, tmp_path):
    from distr.core.project_cli_backends import registry
    from distr.core.project_cli_backends.provider_preflight import ProviderPreflight

    class Backend:
        id = "pi"
        name = "Pi"

        def setup_status(self):
            return SimpleNamespace(
                ready=True,
                message="ready",
                setup_instructions="",
                to_dict=lambda: {"id": "pi", "ready": True},
            )

        async def send_task(self, *_args, **_kwargs):
            raise AssertionError("financial preflight must prevent dispatch")

    monkeypatch.setattr(registry, "get_backend", lambda _backend_id: Backend())
    monkeypatch.setattr(registry, "_git_status_short", lambda _folder: [])
    monkeypatch.setattr("distr.core.terminal.get_project_runtime_snapshot", lambda _project_id: {})
    monkeypatch.setattr("distr.core.settings.load_settings_from_db", lambda: {"openrouter_key": "k"})
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.preflight_provider_route",
        lambda *_args, **_kwargs: ProviderPreflight(
            "openrouter", "tencent/hy3-preview", "blocked", False,
            "OpenRouter reports insufficient credit for this API key.",
        ),
    )
    monkeypatch.setattr("distr.core.kanban.project_execution.create_execution_session", lambda **_kwargs: 99)
    monkeypatch.setattr("distr.core.kanban.project_execution.append_execution_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("distr.core.kanban.project_execution.complete_execution_session", lambda *_args, **_kwargs: None)

    result = asyncio.run(
        registry.run_project_task(
            SimpleNamespace(
                id=1,
                name="Demo",
                folder_location=str(tmp_path),
                coding_backend="pi",
                coding_backend_model="tencent/hy3-preview",
            ),
            "Make the button black",
            backend_id_override="pi",
            model_override="tencent/hy3-preview",
            adapter_options={"model_provider": "openrouter"},
        )
    )

    assert result.success is False
    assert result.waits_for_human is True
    assert "No model work was started" in result.error
    assert "Would you like" in result.error


def test_free_catalogue_ranks_tool_capable_coding_models_and_filters_unfit_models(monkeypatch):
    payload = {"data": [
        {
            "id": "vendor/coder:free", "name": "Coder", "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
            "supported_parameters": ["tools"],
            "architecture": {"output_modalities": ["text"], "input_modalities": ["text"]},
            "benchmarks": {"artificial_analysis": {"coding_index": 50, "agentic_index": 20, "intelligence_index": 30}},
        },
        {
            "id": "vendor/chat:free", "name": "Chat", "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
            "supported_parameters": [],
            "architecture": {"output_modalities": ["text"]},
        },
        {
            "id": "vendor/paid", "name": "Paid", "context_length": 1000000,
            "pricing": {"prompt": "0.1", "completion": "0", "request": "0"},
            "supported_parameters": ["tools"],
            "architecture": {"output_modalities": ["text"]},
        },
    ]}
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )

    candidates = rank_openrouter_free_models(
        api_key="k", complexity="high", required_capabilities=["tools"]
    )

    assert [item["model"] for item in candidates] == ["vendor/coder:free"]
    assert candidates[0]["rank"] == 1
    assert candidates[0]["coding_index"] == 50
    assert "tool calling" in candidates[0]["reason"]


def test_selected_free_model_must_pass_minimal_readiness_request(monkeypatch):
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        lambda *_args, **_kwargs: _Response({"choices": [{"message": {"content": "OK"}}]}),
    )

    report = probe_openrouter_model_readiness(model="vendor/coder:free", api_key="k")

    assert report.ready is True
    assert "accepted" in report.message


def test_selected_free_model_readiness_reports_402_for_retry_ladder(monkeypatch):
    def denied(*_args, **_kwargs):
        payload = io.BytesIO(b'{"error":{"message":"Insufficient credits"}}')
        raise HTTPError("https://openrouter.ai/api/v1/chat/completions", 402, "Payment Required", {}, payload)

    monkeypatch.setattr("distr.core.project_cli_backends.provider_preflight.urlopen", denied)

    report = probe_openrouter_model_readiness(model="vendor/coder:free", api_key="k")

    assert report.ready is False
    assert report.http_status == 402
    assert "Insufficient credits" in report.message


def test_telegram_free_model_options_have_buttons_and_text_selection():
    from distr.core.workflow.interactions import classify_reply, telegram_reply_markup

    interaction = {
        "token": "opaque",
        "allowed_actions": json.dumps(["model_0", "model_1", "model_2", "stop"]),
    }
    markup = telegram_reply_markup(interaction)

    labels = [button["text"] for row in markup["inline_keyboard"] for button in row]
    assert labels == ["Try 1", "Try 2", "Try 3", "Stop"]
    assert classify_reply("try option 2", ["model_0", "model_1", "stop"])[0] == "model_1"
