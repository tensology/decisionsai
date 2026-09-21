import pytest

from distr.core.planning import runtime
from distr.core.turn_runtime.contracts import TurnResult


def run(provider="ollama", messages=None):
    return list(runtime.create_stream(provider, "exact-model", messages or [
        {"role": "system", "content": "Planning rules"}, {"role": "user", "content": "Build a screen"}],
        {}, project_id=9, project_name="Example", chat_id=12, turn_id=13))


@pytest.mark.parametrize("provider", ["ollama", "openrouter"])
def test_selected_pi_route_uses_restricted_runtime_without_execution_bindings(monkeypatch, provider):
    observed = []
    async def execute(request, *, runtime_id):
        observed.append(request)
        assert runtime_id == "cli_harness"
        return TurnResult(True, runtime_id, "pi", request.route.model, output='{"reply":"Done"}')
    monkeypatch.setattr(runtime, "execute_turn", execute)
    monkeypatch.setattr(runtime.llm_factory, "create_stream", lambda *a: pytest.fail("No API fallback"))
    assert run(provider) == ['{"reply":"Done"}']
    request = observed[0]
    assert (request.route.backend_id, request.route.provider, request.route.model) == ("pi", provider, "exact-model")
    assert request.route.adapter_options["proposal_only"] is True
    assert request.required_capabilities == ()
    assert request.scope.ticket_id is None and request.scope.board_id is None
    assert request.scope.chat_id == 12 and request.scope.turn_id == 13
    assert request.project.folder_location == ""
    assert request.route.adapter_options["proposal_system"] == "Planning rules"
    assert "Planning rules" not in request.instruction and "Build a screen" in request.instruction


def test_images_are_transported_separately_from_text_context(monkeypatch):
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
    async def execute(request, **kwargs):
        assert request.route.adapter_options["proposal_images"] == [image]
        assert "base64" not in request.instruction
        assert "Attached image 1" in request.instruction
        assert "Look at this" in request.instruction
        return TurnResult(True, "cli_harness", "pi", "exact-model", output="{}")
    monkeypatch.setattr(runtime, "execute_turn", execute)
    assert run(messages=[{"role": "user", "content": [{"type": "text", "text": "Look at this"}, image]}]) == ["{}"]


@pytest.mark.parametrize("success, waiting", [(False, False), (True, True)])
def test_failed_or_incomplete_cli_never_falls_back_or_yields_proposal(monkeypatch, success, waiting):
    async def execute(*args, **kwargs):
        return TurnResult(success, "cli_harness", "pi", "exact-model", output="partial", waits_for_human=waiting)
    monkeypatch.setattr(runtime, "execute_turn", execute)
    monkeypatch.setattr(runtime.llm_factory, "create_stream", lambda *a: pytest.fail("No fallback"))
    with pytest.raises(RuntimeError, match="complete proposal"):
        run()


def test_other_provider_keeps_its_explicit_api_route(monkeypatch):
    def stream(provider, model, messages, settings):
        assert (provider, model) == ("openai", "exact-model")
        yield "{}"
    monkeypatch.setattr(runtime.llm_factory, "create_stream", stream)
    assert run("openai") == ["{}"]


def test_codex_selection_uses_exact_cli_route_and_catalog_without_api_fallback(monkeypatch):
    monkeypatch.setattr('distr.core.project_cli_backends.codex_proposal.available_models', lambda:['exact-model'])
    async def execute(request, *, runtime_id):
        assert runtime_id == 'cli_harness'
        assert (request.route.backend_id, request.route.provider, request.route.model) == ('codex','openai','exact-model')
        assert request.project.folder_location == ''
        return TurnResult(True, runtime_id, 'codex', 'exact-model', output='{}')
    monkeypatch.setattr(runtime, 'execute_turn', execute)
    monkeypatch.setattr(runtime.llm_factory, 'create_stream', lambda *args:pytest.fail('No API fallback'))
    assert run('codex') == ['{}']
    monkeypatch.setattr('distr.core.project_cli_backends.codex_proposal.available_models', lambda:[])
    with pytest.raises(runtime.PlanningRuntimeError, match='catalog'):
        run('codex')


def test_transport_code_produces_actionable_safe_error(monkeypatch):
    async def execute(*args, **kwargs):
        return TurnResult(False, "cli_harness", "pi", "exact-model", error="Bearer secret",
                          evidence={"proposal_error": {"code": "timeout", "stage": "stream"}})
    monkeypatch.setattr(runtime, "execute_turn", execute)
    with pytest.raises(runtime.PlanningRuntimeError, match="timed out") as error:
        run()
    assert "secret" not in str(error.value)
