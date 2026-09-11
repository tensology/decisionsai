from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

from distr.core.turn_runtime import (
    AnthropicModelAdapter,
    ModelResponse,
    ModelRoute,
    OpenAICompatibleModelAdapter,
    ProjectToolExecutor,
    RetryingModelAdapter,
    TurnRequest,
    TurnScope,
    select_turn_runtime_id,
)


def _chunk(*, content="", tool_calls=(), finish_reason=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=list(tool_calls))
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tool_delta(index, *, call_id=None, name=None, arguments=None):
    function = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=call_id, function=function)


def test_openai_compatible_adapter_streams_text_and_reassembles_tool_calls():
    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return iter(
                [
                    _chunk(content="Checking "),
                    _chunk(
                        tool_calls=(
                            _tool_delta(0, call_id="call-7", name="read_", arguments='{"path":'),
                        )
                    ),
                    _chunk(
                        content="files.",
                        tool_calls=(
                            _tool_delta(0, name="file", arguments='"app.py"}'),
                        ),
                        finish_reason="tool_calls",
                    ),
                ]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    adapter = OpenAICompatibleModelAdapter(
        provider="Kilo",
        model="free-model",
        settings={"kilo_key": "configured"},
        client=client,
    )
    deltas = []
    response = asyncio.run(
        adapter.complete(
            [
                {"role": "user", "content": "Inspect the project."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "prior", "name": "list_files", "arguments": {"path": "."}}],
                },
                {"role": "tool", "tool_call_id": "prior", "content": "app.py"},
            ],
            [{"type": "function", "function": {"name": "read_file"}}],
            on_delta=deltas.append,
        )
    )

    assert captured["stream"] is True
    assert captured["model"] == "free-model"
    assert captured["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"path": "."}'
    assert deltas == ["Checking ", "files."]
    assert response.text == "Checking files."
    assert response.finish_reason == "tool_calls"
    assert response.tool_calls[0].call_id == "call-7"
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "app.py"}


def test_openai_compatible_adapter_requires_concrete_config():
    for kwargs, message in (
        ({"provider": "unknown", "model": "x", "settings": {}}, "Unsupported"),
        ({"provider": "openrouter", "model": "auto", "settings": {"openrouter_key": "x"}}, "concrete model"),
        ({"provider": "nvidia", "model": "model", "settings": {}}, "No API key"),
    ):
        try:
            OpenAICompatibleModelAdapter(**kwargs)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("invalid native provider configuration was accepted")


def test_anthropic_adapter_streams_text_and_normalizes_native_tool_calls():
    captured = {}
    final = SimpleNamespace(
        content=[SimpleNamespace(type="text"), SimpleNamespace(type="tool_use", id="tool-9", name="read_file", input={"path": "app.py"})],
        stop_reason="tool_use",
        usage=SimpleNamespace(model_dump=lambda: {"input_tokens": 12, "output_tokens": 4}),
    )

    class Stream:
        text_stream = iter(["Checking ", "the file."])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_final_message(self):
            return final

    class Messages:
        def stream(self, **kwargs):
            captured.update(kwargs)
            return Stream()

    client = SimpleNamespace(messages=Messages())
    adapter = AnthropicModelAdapter(
        model="claude-sonnet-4-5",
        settings={"anthropic_key": "configured"},
        client=client,
    )
    deltas = []
    response = asyncio.run(
        adapter.complete(
            [
                {"role": "system", "content": "Work in the project."},
                {"role": "user", "content": "Inspect it."},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "prior", "name": "list_files", "arguments": {"path": "."}}],
                },
                {"role": "tool", "tool_call_id": "prior", "content": "app.py"},
            ],
            [{"type": "function", "function": {"name": "read_file", "description": "Read", "parameters": {"type": "object"}}}],
            on_delta=deltas.append,
        )
    )

    assert captured["system"] == "Work in the project."
    assert captured["tools"][0]["input_schema"] == {"type": "object"}
    assert captured["messages"][2]["content"][0]["type"] == "tool_result"
    assert deltas == ["Checking ", "the file."]
    assert response.text == "Checking the file."
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {"path": "app.py"}


def test_ollama_native_adapter_does_not_require_a_remote_api_key():
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    adapter = OpenAICompatibleModelAdapter(
        provider="ollama",
        model="qwen3-coder",
        settings={},
        client=client,
    )

    assert adapter.provider == "ollama"


def test_retrying_adapter_retries_transient_failure_before_output():
    class RateLimitError(RuntimeError):
        status_code = 429

    class FlakyAdapter:
        adapter_id = "flaky"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools, *, on_delta=None):
            self.calls += 1
            if self.calls < 3:
                raise RateLimitError("rate limited")
            on_delta("Ready.")
            return ModelResponse(text="Ready.")

    adapter = FlakyAdapter()
    result = asyncio.run(RetryingModelAdapter(adapter).complete([], [], on_delta=lambda _delta: None))

    assert result.text == "Ready."
    assert adapter.calls == 3


def test_retrying_adapter_does_not_repeat_a_partially_streamed_response():
    class ConnectionErrorAfterOutput(RuntimeError):
        pass

    class PartialAdapter:
        adapter_id = "partial"

        def __init__(self):
            self.calls = 0

        async def complete(self, messages, tools, *, on_delta=None):
            self.calls += 1
            on_delta("Partial")
            raise ConnectionErrorAfterOutput("connection reset")

    adapter = PartialAdapter()
    try:
        asyncio.run(RetryingModelAdapter(adapter).complete([], [], on_delta=lambda _delta: None))
    except ConnectionErrorAfterOutput:
        pass
    else:
        raise AssertionError("partially streamed failure was hidden")

    assert adapter.calls == 1


def test_project_tools_are_scoped_and_support_exact_atomic_edits(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "app.py"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    ignored = root / "node_modules" / "package.js"
    ignored.parent.mkdir()
    ignored.write_text("ignored", encoding="utf-8")
    tools = ProjectToolExecutor(str(root))

    listed = asyncio.run(tools.execute("list_files", {}))
    read = asyncio.run(tools.execute("read_file", {"path": "app.py", "start_line": 2, "end_line": 3}))
    replaced = asyncio.run(
        tools.execute(
            "replace_text",
            {"path": "app.py", "old_text": "two", "new_text": "TWO", "expected_replacements": 1},
        )
    )
    created = asyncio.run(tools.execute("write_file", {"path": "src/new.py", "content": "ready = True\n"}))
    refused = asyncio.run(tools.execute("write_file", {"path": "app.py", "content": "lost\n"}))
    escaped = asyncio.run(tools.execute("read_file", {"path": "../outside.txt"}))

    assert "app.py" in listed.artifacts
    assert all("node_modules" not in path for path in listed.artifacts)
    assert read.output == "2: two\n3: three"
    assert replaced.status == "success"
    assert source.read_text(encoding="utf-8") == "one\nTWO\nthree\n"
    assert created.status == "success"
    assert (root / "src" / "new.py").read_text(encoding="utf-8") == "ready = True\n"
    assert refused.status == "error"
    assert escaped.status == "error"
    assert "escapes" in escaped.error


def test_project_tools_read_only_mode_removes_mutating_actions(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    tools = ProjectToolExecutor(str(root), read_only=True)
    names = {definition["function"]["name"] for definition in tools.definitions()}

    assert {"read_file", "list_files", "search_files"}.issubset(names)
    assert {"replace_text", "write_file", "run_command"}.isdisjoint(names)
    result = asyncio.run(tools.execute("run_command", {"command": "pwd"}))
    assert result.status == "error"
    assert "not available" in result.summary


def test_untrusted_external_runtime_exposes_no_project_or_automation_tools(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    tools = ProjectToolExecutor(
        str(root),
        automation_id="auto_7",
        automation_thread_id=17,
        automation_turn_id=20,
        untrusted_external=True,
    )

    assert tools.definitions() == []
    assert asyncio.run(tools.execute("read_file", {"path": "secrets.txt"})).status == "error"
    assert asyncio.run(tools.execute("automation_control", {"action": "delete"})).status == "error"


def test_automation_thread_gets_server_bound_schedule_control(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    calls = []

    def fake_run(self, **kwargs):
        calls.append(kwargs)
        return json.dumps({"surface": "development", "updated": True, "automation_id": kwargs["automation_id"]})

    monkeypatch.setattr(
        "distr.core.agent.tools.integrations.development_control.DevelopmentControlTool._run",
        fake_run,
    )
    tools = ProjectToolExecutor(str(root), automation_id="auto_42")
    definition = next(item for item in tools.definitions() if item["function"]["name"] == "automation_control")

    result = asyncio.run(tools.execute(
        "automation_control",
        {"action": "update", "schedule": {"kind": "weekdays", "time": "08:00"}},
    ))

    assert "automation_id" not in definition["function"]["parameters"]["properties"]
    assert calls[0]["automation_id"] == "auto_42"
    assert calls[0]["action"] == "update_automation"
    assert result.status == "success"


def test_bound_automation_control_pauses_edits_runs_and_confirms_delete(tmp_path, monkeypatch):
    from distr.core.automation.store import create_automation, get_automation
    from distr.core.automation_orchestrator import ensure_automation_thread

    root = tmp_path / "project"
    root.mkdir()
    automation = create_automation(
        name="Owned schedule",
        automation_type="scheduled_instruction",
        status="active",
        instruction="Inspect the project.",
        preset_id="",
        schedule={"kind": "daily", "time": "09:00"},
        action_config={},
    )
    thread_id = ensure_automation_thread(automation)
    monkeypatch.setattr(
        "distr.core.automation_orchestrator.dispatch_automation_to_current_chat",
        lambda selected, manual: {"status": "running", "automation_id": selected["id"]},
    )
    tools = ProjectToolExecutor(
        str(root),
        automation_id=automation["id"],
        automation_thread_id=thread_id,
        automation_turn_id=100,
    )

    paused = asyncio.run(tools.execute("automation_control", {"action": "pause"}))
    updated = asyncio.run(tools.execute(
        "automation_control",
        {"action": "update", "schedule": {"kind": "weekly", "time": "08:00", "days": "1,2,3,4,5"}},
    ))
    ran = asyncio.run(tools.execute("automation_control", {"action": "run"}))
    challenged = asyncio.run(tools.execute("automation_control", {"action": "delete"}))
    token = challenged.output["confirmation_token"]
    same_turn = asyncio.run(tools.execute(
        "automation_control",
        {"action": "delete", "confirm": True, "confirmation_token": token},
    ))
    token = same_turn.output["confirmation_token"]
    later_turn_tools = ProjectToolExecutor(
        str(root),
        automation_id=automation["id"],
        automation_thread_id=thread_id,
        automation_turn_id=101,
    )
    deleted = asyncio.run(later_turn_tools.execute(
        "automation_control",
        {"action": "delete", "confirm": True, "confirmation_token": token},
    ))

    assert paused.status == "success"
    assert updated.status == "success"
    assert ran.output["run"]["automation_id"] == automation["id"]
    assert challenged.status == "warning"
    assert same_turn.status == "warning"
    assert deleted.output["deleted"] is True
    assert get_automation(automation["id"]) is None


def test_project_command_runs_through_rtk_layer_in_project_directory(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    captured = {}

    def fake_run(command, *, cwd=None, timeout=60, env=None, cancel_event=None):
        captured.update(command=command, cwd=cwd, timeout=timeout, cancellable=cancel_event is not None)
        return SimpleNamespace(returncode=0, stdout="tests passed\n", stderr="")

    monkeypatch.setattr("distr.core.rtk_support.run_cancellable_shell_command", fake_run)
    tools = ProjectToolExecutor(str(root))
    result = asyncio.run(
        tools.execute(
            "run_command",
            {"command": "pytest -q", "working_directory": ".", "timeout_seconds": 45},
        )
    )

    assert captured == {"command": "pytest -q", "cwd": str(root), "timeout": 45, "cancellable": True}
    assert result.status == "success"
    assert result.output == "tests passed\n"


def test_project_command_refuses_destructive_shell_operations(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(
        "distr.core.rtk_support.run_cancellable_shell_command",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("destructive command executed")),
    )
    tools = ProjectToolExecutor(str(root))

    result = asyncio.run(tools.execute("run_command", {"command": "rm -rf ."}))

    assert result.status == "error"
    assert "Refused a destructive" in result.summary


def test_rtk_command_runner_terminates_process_group_on_cancellation(tmp_path, monkeypatch):
    from distr.core.rtk_support import run_cancellable_shell_command

    monkeypatch.setattr("distr.core.rtk_support.rewrite_shell_command", lambda command: command)
    cancelled = threading.Event()
    timer = threading.Timer(0.2, cancelled.set)
    timer.start()
    started = time.monotonic()
    result = run_cancellable_shell_command(
        "python3 -c 'import time; time.sleep(10)'",
        cwd=str(tmp_path),
        timeout=20,
        cancel_event=cancelled,
    )
    timer.cancel()

    assert result.returncode == 130
    assert "cancelled by the user" in result.stderr
    assert time.monotonic() - started < 3


def test_native_project_tools_discover_and_read_trusted_harness_skills(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    home = tmp_path / "home"
    skill = home / ".decisions" / "skills" / "humanizer"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Humanizer\n\nRemove robotic phrasing.\n", encoding="utf-8")
    registry = home / ".decisions" / "harness" / "community-skills-registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        '[{"id":"humanizer","path":"' + str(skill).replace('\\', '\\\\') + '"}]',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    tools = ProjectToolExecutor(str(root), read_only=True)

    listed = asyncio.run(tools.execute("list_harness_skills", {"query": "human"}))
    broad = asyncio.run(tools.execute("list_harness_skills", {}))
    loaded = asyncio.run(tools.execute("read_harness_skill", {"skill_id": "humanizer"}))

    assert listed.artifacts == ("humanizer",)
    assert broad.status == "error"
    assert "focused query" in broad.summary
    assert "Remove robotic phrasing" in loaded.output
    assert "read_harness_skill" in {definition["function"]["name"] for definition in tools.definitions()}


def test_native_project_tools_read_only_explicit_turn_attachments(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "project"
    root.mkdir()
    attachment = home / ".decisions" / "workspaces" / "projects" / "7" / "attachments" / "notes.md"
    attachment.parent.mkdir(parents=True)
    attachment.write_text("# Reference\n\nUse the compact layout.\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    tools = ProjectToolExecutor(
        str(root),
        read_only=True,
        attachments=[{"name": "notes.md", "path": str(attachment), "mime_type": "text/markdown"}],
    )

    listed = asyncio.run(tools.execute("list_attachments", {}))
    read = asyncio.run(tools.execute("read_attachment", {"path": str(attachment)}))
    refused = asyncio.run(tools.execute("read_attachment", {"path": str(tmp_path / "private.txt")}))

    assert "notes.md" in listed.output
    assert "Use the compact layout" in read.output
    assert refused.status == "error"
    assert {"list_attachments", "read_attachment"}.issubset(
        {definition["function"]["name"] for definition in tools.definitions()}
    )


def test_anthropic_adapter_converts_data_url_images_to_native_blocks():
    blocks = AnthropicModelAdapter._content_blocks(
        [
            {"type": "text", "text": "What is shown?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,ZmFrZQ=="}},
        ]
    )

    assert blocks[0] == {"type": "text", "text": "What is shown?"}
    assert blocks[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "ZmFrZQ=="},
    }


def test_runtime_selection_only_enables_native_for_concrete_supported_route(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    base = dict(
        instruction="Build it.",
        project=SimpleNamespace(id=1, folder_location=str(root)),
        scope=TurnScope(chat_id=2, project_id=1, project_folder=str(root)),
    )
    monkeypatch.delenv("DECISIONSAI_DEVELOPMENT_TURN_RUNTIME", raising=False)

    native = TurnRequest(**base, route=ModelRoute(provider="kilocode", model="free-model", backend_id="pi"))
    anthropic = TurnRequest(**base, route=ModelRoute(provider="anthropic", model="claude-sonnet-4-5", backend_id="claude"))
    missing_provider = TurnRequest(**base, route=ModelRoute(provider="", model="auto", backend_id="codex"))
    unsupported = TurnRequest(**base, route=ModelRoute(provider="custom", model="model", backend_id="pi"))

    assert select_turn_runtime_id(native) == "native"
    assert select_turn_runtime_id(anthropic) == "native"
    assert select_turn_runtime_id(missing_provider) == "cli_harness"
    assert select_turn_runtime_id(unsupported) == "cli_harness"
