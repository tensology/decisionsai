from __future__ import annotations

import json

import pytest
from langchain_core.tools import BaseTool

from distr.core.agent.tools.artifacts import (
    ArtifactTool,
    ToolArtifactCompiler,
    ToolArtifactStore,
    validate_artifact,
)


class EchoTool(BaseTool):
    name: str = "echo"
    description: str = "Echo a value"

    def _run(self, value: str = "", **kwargs):
        return value


def _artifact(*, steps=None):
    return {
        "name": "repeat_values",
        "description": "Repeat values through deterministic steps",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        "steps": steps or [
            {
                "id": "first",
                "name": "Read input",
                "kind": "tool",
                "tool": "echo",
                "arguments": {"value": "${inputs.value}"},
            },
            {
                "id": "second",
                "name": "Reuse output",
                "kind": "tool",
                "tool": "echo",
                "arguments": {"value": "${steps.first.output}"},
            },
        ],
    }


def test_store_writes_immutable_version_and_latest_pointer(tmp_path):
    store = ToolArtifactStore(tmp_path)
    saved = store.save(_artifact())
    assert saved["name"] == "built__repeat_values"
    assert len(saved["version"]) == 12
    assert (tmp_path / saved["name"] / f"{saved['version']}.json").exists()
    assert store.list_latest()[0]["content_hash"] == saved["content_hash"]


def test_artifact_executes_ordered_steps_and_resolves_previous_output(monkeypatch):
    echo = EchoTool()
    monkeypatch.setattr(
        "distr.core.agent.tools.loader.get_cached_tool",
        lambda name: echo if name == "echo" else None,
    )
    result = ArtifactTool(_artifact()).invoke({"value": "alpha"})
    assert "1. Read input: completed" in result
    assert "2. Reuse output: completed" in result
    assert result.endswith("alpha")


def test_python_step_is_prefixed_with_json_inputs_and_uses_safe_executor(monkeypatch):
    captured = {}

    class ExecuteTool(BaseTool):
        name: str = "execute_code"
        description: str = "Execute code"

        def _run(self, code: str, description: str = "", **kwargs):
            captured["code"] = code
            return "ok"

    executor = ExecuteTool()
    monkeypatch.setattr(
        "distr.core.agent.tools.loader.get_cached_tool",
        lambda name: executor if name == "execute_code" else None,
    )
    tool = ArtifactTool(_artifact(steps=[{
        "id": "python",
        "name": "Transform",
        "kind": "python",
        "code": "result = artifact_inputs['value'].upper()",
    }]))
    assert "completed" in tool.invoke({"value": "alpha"})
    assert "artifact_inputs = json.loads" in captured["code"]
    assert "alpha" in captured["code"]


def test_compiler_reuses_identical_request_without_second_llm_call(tmp_path, monkeypatch):
    calls = []

    def llm_call(prompt):
        calls.append(prompt)
        return json.dumps(_artifact())

    echo = EchoTool()
    monkeypatch.setattr("distr.core.agent.tools.artifacts._candidate_tools", lambda request: [echo])
    monkeypatch.setattr(
        "distr.core.agent.tools.loader.get_cached_tool",
        lambda name: echo if name == "echo" else None,
    )
    compiler = ToolArtifactCompiler(ToolArtifactStore(tmp_path), llm_call=llm_call)
    first, reused_first = compiler.compile("Repeat this value through two steps")
    second, reused_second = compiler.compile("Repeat this value through two steps")
    assert reused_first is False
    assert reused_second is True
    assert first["content_hash"] == second["content_hash"]
    assert len(calls) == 1


def test_validation_rejects_recursive_or_bulk_delete_steps():
    bad = _artifact(steps=[{
        "id": "recursive",
        "kind": "tool",
        "tool": "build_tool",
        "arguments": {},
    }])
    with pytest.raises(ValueError):
        validate_artifact(bad)

    destructive = _artifact(steps=[{
        "id": "delete",
        "kind": "python",
        "code": "import shutil\nshutil.rmtree('/tmp/example')",
    }])
    with pytest.raises(ValueError):
        validate_artifact(destructive)
