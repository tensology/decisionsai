import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from distr.core.project_cli_backends.provider_preflight import (
    preflight_provider_route,
    probe_openrouter_model_readiness,
    rank_openrouter_free_models,
)
from distr.core.workflow.step_executor import (
    _hosted_free_recommendation,
    _provider_rate_limited,
    _ticket_requires_read_only_execution,
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


def test_provider_preflight_prompt_never_offers_an_unavailable_action():
    source = Path("distr/core/workflow/step_executor.py").read_text(encoding="utf-8")

    assert "Approve to use it, choose another model, or Stop." not in source
    assert "Choose one of the model options below" in source


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


def test_free_catalogue_filters_text_only_models_for_visual_evidence(monkeypatch):
    def model(model_id, inputs):
        return {
            "id": model_id,
            "name": model_id,
            "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
            "supported_parameters": ["tools"],
            "architecture": {
                "output_modalities": ["text"],
                "input_modalities": inputs,
            },
        }

    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        lambda *_args, **_kwargs: _Response({"data": [
            model("vendor/text-120b:free", ["text"]),
            model("vendor/vision-30b:free", ["text", "image"]),
        ]}),
    )

    candidates = rank_openrouter_free_models(
        api_key="k",
        complexity="high",
        required_capabilities=["tools", "vision"],
    )

    assert [item["model"] for item in candidates] == ["vendor/vision-30b:free"]
    assert candidates[0]["input_modalities"] == ["image", "text"]


def test_concrete_text_only_openrouter_model_is_blocked_before_visual_review(monkeypatch):
    def respond(request, **_kwargs):
        if "/models" in request.full_url:
            return _Response({"data": [{
                "id": "vendor/text-reviewer",
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            }]})
        if request.full_url.endswith("/credits"):
            return _Response({"data": {"total_credits": 10, "total_usage": 0}})
        return _Response({"data": {"limit_remaining": 10}})

    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        respond,
    )
    route = _route("vendor/text-reviewer")
    route["evidence_capabilities"] = ["vision"]

    report = preflight_provider_route(
        route,
        settings={"openrouter_key": "k"},
        complexity="high",
    )

    assert report.ready is False
    assert report.status == "blocked"
    assert "text-only" in report.message
    assert "visual-evidence" in report.message


def test_hosted_auto_prefers_stronger_capable_free_model_when_benchmarks_are_sparse(monkeypatch):
    def model(model_id, name, *, context=131072):
        return {
            "id": model_id,
            "name": name,
            "context_length": context,
            "pricing": {"prompt": "0", "completion": "0", "request": "0"},
            "supported_parameters": ["tools"],
            "architecture": {"output_modalities": ["text"], "input_modalities": ["text"]},
        }

    # Deliberately put the small model first, matching the failure mode where
    # sparse OpenRouter metadata previously left selection to catalogue/id order.
    payload = {"data": [
        model("google/gemma-4-26b-it:free", "Gemma 4 26B"),
        model("vendor/command-120b:free", "Command 120B"),
        model("vendor/coder-70b:free", "Coder 70B"),
    ]}
    monkeypatch.setattr(
        "distr.core.project_cli_backends.provider_preflight.urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )

    candidates = rank_openrouter_free_models(
        api_key="k", complexity="high", required_capabilities=["tools"], limit=3
    )

    assert [item["model"] for item in candidates] == [
        "vendor/command-120b:free",
        "vendor/coder-70b:free",
        "google/gemma-4-26b-it:free",
    ]
    assert candidates[0]["deployment_scope"] == "hosted"
    assert candidates[0]["capacity_policy"] == "prefer_strongest_capable"
    assert "120B hosted capacity" in candidates[0]["reason"]


def test_hosted_recommendation_explains_capacity_and_honestly_labels_small_fallback():
    large = _hosted_free_recommendation(
        {
            "name": "Command 120B",
            "parameter_billions": 120,
            "supports_tools": True,
            "context_length": 131072,
        },
        complexity="high",
    )
    last_resort = _hosted_free_recommendation(
        {
            "name": "Gemma 4 26B",
            "parameter_billions": 26,
            "supports_tools": True,
            "context_length": 131072,
        },
        complexity="high",
    )

    assert "Mac's memory is not the size limit" in large
    assert "strongest healthy compatible" in large
    assert "120B" in large
    assert "stronger compatible hosted free models are unavailable" in last_resort
    assert "best remaining" in last_resort


def test_provider_rate_limit_is_treated_as_provider_wide_not_model_specific():
    assert _provider_rate_limited(http_status=429) is True
    assert _provider_rate_limited(message="429 Rate limit exceeded") is True
    assert _provider_rate_limited(
        message="429 Provider returned error: model is temporarily rate-limited upstream"
    ) is False
    assert _provider_rate_limited(message="model not found") is False


def test_explicit_ticket_wide_read_only_contract_survives_implementation_steps():
    assert _ticket_requires_read_only_execution(
        "Run this through the workflow as a strictly read-only verification ticket. "
        "No implementation change is requested."
    ) is True
    assert _ticket_requires_read_only_execution("Implement the checkout fix and test it.") is False


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
