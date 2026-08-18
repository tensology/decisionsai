"""Durable deterministic tools compiled from missing user capabilities.

The LLM is used once to produce a small declarative artifact. Ordinary runs
execute the frozen artifact, never regenerate it. Existing Decisions tools
remain the preferred building blocks; frozen Python is the escape hatch and is
executed through ``execute_code`` so its existing safety confirmation applies.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from distr.core.paths import TOOL_ARTIFACTS_DIR

logger = logging.getLogger(__name__)

ARTIFACT_FORMAT_VERSION = "1.0"
MAX_ARTIFACT_STEPS = 12
MAX_GENERATED_CODE_CHARS = 40_000
_RESERVED_TOOLS = {"request_tool", "build_tool"}
_EXACT_TEMPLATE = re.compile(r"^\$\{([^}]+)\}$")
_INLINE_TEMPLATE = re.compile(r"\$\{([^}]+)\}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str, default: str = "capability") -> str:
    value = re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")
    return (value or default)[:48]


def _request_hash(request: str) -> str:
    normalized = " ".join((request or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in payload.items()
        if key not in {"created_at", "content_hash", "version"}
    }
    encoded = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


class ToolArtifactStore:
    """Filesystem store for immutable artifact versions plus latest pointers."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or TOOL_ARTIFACTS_DIR).expanduser()

    def save(self, artifact: dict[str, Any]) -> dict[str, Any]:
        checked = validate_artifact(artifact)
        content_hash = _canonical_hash(checked)
        checked["content_hash"] = content_hash
        checked["version"] = content_hash[:12]
        checked.setdefault("created_at", _utc_now())
        tool_dir = self.root / checked["name"]
        version_path = tool_dir / f"{checked['version']}.json"
        if not version_path.exists():
            _atomic_json_write(version_path, checked)
        _atomic_json_write(tool_dir / "latest.json", checked)
        return checked

    def list_latest(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        artifacts: list[dict[str, Any]] = []
        for latest in sorted(self.root.glob("*/latest.json")):
            try:
                artifacts.append(validate_artifact(json.loads(latest.read_text(encoding="utf-8"))))
            except Exception:
                logger.warning("Ignoring invalid tool artifact %s", latest, exc_info=True)
        return artifacts

    def find_by_request(self, request: str) -> Optional[dict[str, Any]]:
        digest = _request_hash(request)
        return next((item for item in self.list_latest() if item.get("request_hash") == digest), None)


def _schema_field(alias: str, spec: dict[str, Any], required: bool) -> tuple[Any, Any]:
    kind = str(spec.get("type") or "string")
    annotation: Any = {
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list[Any],
        "object": dict[str, Any],
    }.get(kind, str)
    description = str(spec.get("description") or "")[:1000]
    if required:
        return annotation, Field(..., alias=alias, description=description)
    default: Any = spec.get("default")
    if default is None:
        default = [] if kind == "array" else {} if kind == "object" else None
    return Optional[annotation], Field(default=default, alias=alias, description=description)


def artifact_args_model(artifact: dict[str, Any]) -> type[BaseModel]:
    schema = artifact.get("input_schema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    properties = properties if isinstance(properties, dict) else {}
    required = {str(item) for item in (schema.get("required") or [])}
    fields: dict[str, Any] = {}
    for alias, raw_spec in properties.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(alias)):
            raise ValueError(f"Artifact input name must be a Python identifier: {alias}")
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        fields[str(alias)] = _schema_field(str(alias), spec, str(alias) in required)
    if not fields:
        fields["request"] = (Optional[str], Field(default="", description="Optional runtime context"))
    return create_model(
        f"ArtifactArgs_{_slug(artifact.get('name', 'tool'))}",
        __config__=ConfigDict(populate_by_name=True, extra="forbid"),
        **fields,
    )


def _infer_permissions(steps: list[dict[str, Any]]) -> list[str]:
    permissions: set[str] = set()
    for step in steps:
        if step.get("kind") == "python":
            permissions.add("code_execution")
        tool_name = str(step.get("tool") or "")
        if any(token in tool_name for token in ("file", "document", "audio", "video")):
            permissions.add("filesystem")
        if any(token in tool_name for token in ("web", "http", "playwright", "workspace")):
            permissions.add("network")
        if any(token in tool_name for token in ("mouse", "screen", "computer_use", "window", "type_text", "key")):
            permissions.add("computer_use")
        if any(token in tool_name for token in ("telegram", "gmail", "calendar", "ticket", "git")):
            permissions.add("external_service")
    return sorted(permissions)


def validate_artifact(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Tool artifact must be a JSON object")
    artifact = dict(raw)
    artifact["format_version"] = ARTIFACT_FORMAT_VERSION
    artifact["name"] = "built__" + _slug(str(artifact.get("name") or artifact.get("title") or "capability").removeprefix("built__"))
    artifact["description"] = str(artifact.get("description") or "Agent-built deterministic capability").strip()[:2000]
    schema = artifact.get("input_schema") or {"type": "object", "properties": {}}
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise ValueError("input_schema must be an object JSON Schema")
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict) or len(properties) > 12:
        raise ValueError("input_schema.properties must contain at most 12 fields")
    schema["properties"] = properties
    schema["required"] = [str(item) for item in (schema.get("required") or []) if str(item) in properties]
    artifact["input_schema"] = schema

    steps = artifact.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_ARTIFACT_STEPS:
        raise ValueError(f"A tool artifact requires 1 to {MAX_ARTIFACT_STEPS} steps")
    seen_ids: set[str] = set()
    checked_steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, dict):
            raise ValueError(f"Step {index} must be an object")
        step = dict(raw_step)
        step_id = _slug(str(step.get("id") or f"step_{index}"), f"step_{index}")
        if step_id in seen_ids:
            raise ValueError(f"Duplicate step id: {step_id}")
        seen_ids.add(step_id)
        step["id"] = step_id
        step["name"] = str(step.get("name") or step_id.replace("_", " ").title())[:160]
        kind = str(step.get("kind") or ("python" if step.get("code") else "tool")).lower()
        if kind not in {"tool", "python"}:
            raise ValueError(f"Unsupported step kind: {kind}")
        step["kind"] = kind
        if kind == "tool":
            tool_name = str(step.get("tool") or "").strip()
            if not tool_name or tool_name in _RESERVED_TOOLS or tool_name.startswith("built__"):
                raise ValueError(f"Step {step_id} references an unsafe or missing tool")
            step["tool"] = tool_name
            arguments = step.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError(f"Step {step_id} arguments must be an object")
            step["arguments"] = arguments
        else:
            code = str(step.get("code") or "")
            if not code.strip() or len(code) > MAX_GENERATED_CODE_CHARS:
                raise ValueError(f"Step {step_id} requires bounded Python code")
            ast.parse(code)
            from distr.core.files.user_library_guard import scan_execute_code_forbidden_bulk_delete

            refusal = scan_execute_code_forbidden_bulk_delete(code)
            if refusal:
                raise ValueError(refusal)
            step["code"] = code
        checked_steps.append(step)
    artifact["steps"] = checked_steps
    artifact["permissions"] = _infer_permissions(checked_steps)
    artifact["request_hash"] = str(artifact.get("request_hash") or "")
    artifact["source_request"] = str(artifact.get("source_request") or "")[:8000]
    return artifact


def _lookup(path: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ValueError(f"Unknown artifact template value: {path}")
        current = current[segment]
    return current


def _resolve_templates(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_templates(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(item, context) for item in value]
    if not isinstance(value, str):
        return value
    exact = _EXACT_TEMPLATE.match(value)
    if exact:
        return _lookup(exact.group(1), context)
    return _INLINE_TEMPLATE.sub(lambda match: str(_lookup(match.group(1), context)), value)


def _current_chat_id(chat_manager: Any) -> Optional[int]:
    try:
        value = chat_manager.get_current_chat() if chat_manager else None
        return int(value) if value is not None else None
    except Exception:
        return None


class ArtifactTool(BaseTool):
    """LangChain adapter for a frozen multi-step artifact."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(self, artifact: dict[str, Any], chat_manager: Any = None) -> None:
        checked = validate_artifact(artifact)
        super().__init__(
            name=checked["name"],
            description=checked["description"],
            args_schema=artifact_args_model(checked),
        )
        object.__setattr__(self, "_artifact", checked)
        object.__setattr__(self, "_chat_manager", chat_manager)

    @property
    def artifact(self) -> dict[str, Any]:
        return dict(self._artifact)

    def _run(self, **kwargs: Any) -> str:
        validated = self.args_schema.model_validate(kwargs)
        inputs = validated.model_dump(by_alias=True, exclude_none=True)
        context: dict[str, Any] = {"inputs": inputs, "steps": {}}
        chat_id = _current_chat_id(self._chat_manager)
        summaries: list[str] = []

        from distr.core.agent.tools.loader import get_cached_tool

        for index, step in enumerate(self._artifact["steps"], start=1):
            step_name = str(step.get("name") or step["id"])
            event_id = None
            if chat_id:
                try:
                    from distr.core.agent.tool_audit import record_tool_start

                    event_id = record_tool_start(
                        chat_id,
                        f"artifact_step__{step['id']}",
                        instruction_hint=f"Step {index}: {step_name}",
                        routing_path=self.name,
                        metadata={"artifact": self.name, "step": step["id"], "position": index},
                    )
                except Exception:
                    logger.debug("Could not record artifact step start", exc_info=True)
            try:
                if step["kind"] == "python":
                    tool = get_cached_tool("execute_code")
                    if tool is None:
                        raise RuntimeError("Safe code executor is unavailable")
                    encoded_inputs = json.dumps(inputs, ensure_ascii=False)
                    encoded_results = json.dumps(context["steps"], ensure_ascii=False)
                    prefix = (
                        "import json\n"
                        f"artifact_inputs = json.loads({encoded_inputs!r})\n"
                        f"artifact_steps = json.loads({encoded_results!r})\n"
                    )
                    result = str(tool.invoke({"code": prefix + step["code"], "description": step_name}))
                else:
                    tool = get_cached_tool(step["tool"])
                    if tool is None:
                        raise RuntimeError(f"Required tool '{step['tool']}' is unavailable")
                    arguments = _resolve_templates(step.get("arguments") or {}, context)
                    result = str(tool.invoke(arguments))
                from distr.core.agent.tool_audit import classify_tool_result_status

                status = classify_tool_result_status(result)
                context["steps"][step["id"]] = {"output": result, "status": status}
                summaries.append(f"{index}. {step_name}: {status}")
                if event_id:
                    from distr.core.chat_turns import finish_tool

                    finish_tool(event_id, success=status not in {"failed", "error", "cancelled"}, summary=result[:1200], detail=result)
                if status in {"failed", "error", "cancelled", "waiting_for_user"}:
                    return f"Artifact '{self.name}' stopped at {step_name}.\n{result}"
            except Exception as exc:
                message = f"Error: {exc}"
                if event_id:
                    try:
                        from distr.core.chat_turns import finish_tool

                        finish_tool(event_id, success=False, summary=message, detail=message)
                    except Exception:
                        pass
                return f"Artifact '{self.name}' stopped at {step_name}.\n{message}"
        final_output = context["steps"][self._artifact["steps"][-1]["id"]]["output"]
        return "Completed deterministic tool steps:\n" + "\n".join(summaries) + f"\n\nResult:\n{final_output}"

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)


def load_artifact_tools(chat_manager: Any = None, store: ToolArtifactStore | None = None) -> list[ArtifactTool]:
    return [ArtifactTool(item, chat_manager=chat_manager) for item in (store or ToolArtifactStore()).list_latest()]


def _tool_schema(tool: BaseTool) -> dict[str, Any]:
    try:
        schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
    except Exception:
        schema = {}
    if not schema:
        try:
            signature = inspect.signature(tool._run)
            properties = {
                name: {"type": "string"}
                for name, parameter in signature.parameters.items()
                if name != "self" and parameter.kind not in {inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL}
            }
            schema = {"type": "object", "properties": properties}
        except Exception:
            schema = {"type": "object", "properties": {}}
    return schema


def _candidate_tools(request: str, limit: int = 28) -> list[BaseTool]:
    from distr.core.agent.tools.loader import get_warmed_tools_list

    tools = [tool for tool in get_warmed_tools_list() if tool.name not in _RESERVED_TOOLS and not tool.name.startswith("built__")]
    query_tokens = set(re.findall(r"[a-z0-9_]+", request.lower()))
    ranked = sorted(
        tools,
        key=lambda tool: sum(
            1 for token in query_tokens
            if token in f"{tool.name} {getattr(tool, 'description', '')}".lower()
        ),
        reverse=True,
    )
    essentials = {"execute_code", "computer_use", "screenshot_analyzer", "file_operations", "smart_open", "playwright"}
    selected = [tool for tool in ranked[:limit] if tool.name not in essentials]
    selected.extend(tool for tool in tools if tool.name in essentials and tool not in selected)
    return selected[: limit + len(essentials)]


class ToolArtifactCompiler:
    """Compile a missing capability once into an immutable artifact."""

    def __init__(self, store: ToolArtifactStore | None = None, llm_call: Callable[[str], str] | None = None) -> None:
        self.store = store or ToolArtifactStore()
        self._llm_call = llm_call

    def compile(self, request: str, *, name_hint: str = "", force_rebuild: bool = False) -> tuple[dict[str, Any], bool]:
        request = (request or "").strip()
        if len(request) < 8:
            raise ValueError("Describe the missing capability in at least eight characters")
        if not force_rebuild:
            existing = self.store.find_by_request(request)
            if existing:
                return existing, True

        catalog = []
        for tool in _candidate_tools(request):
            catalog.append({
                "name": tool.name,
                "description": str(getattr(tool, "description", ""))[:1000],
                "input_schema": _tool_schema(tool),
            })
        prompt = f"""You compile durable deterministic tools for DecisionsAI.

User capability request:
{request}

Available building blocks:
{json.dumps(catalog, ensure_ascii=False)}

Return ONLY one JSON object with this exact shape:
{{
  "name": "short capability name",
  "description": "when the agent should call it",
  "input_schema": {{"type":"object","properties":{{}},"required":[]}},
  "steps": [
    {{"id":"step_1","name":"human visible action","kind":"tool","tool":"exact_available_tool_name","arguments":{{}}}},
    {{"id":"step_2","name":"human visible action","kind":"python","code":"complete Python using artifact_inputs and artifact_steps"}}
  ]
}}

Rules:
- Break multi-action requests into ordered, individually visible steps.
- Prefer available tools. Use kind=python only for a genuinely missing primitive.
- Every tool argument must match that tool's input schema.
- Reference runtime inputs with ${{inputs.field}} and earlier results with ${{steps.step_id.output}}.
- Python is frozen and run through the safety-checked executor. It may read artifact_inputs and artifact_steps and should set result or print output.
- Never generate recursive deletion, credential access, purchases, deployments, or message sending without using an existing approval-aware tool.
- Keep the tool reusable. Do not hard-code incidental values that should be inputs.
- Use no more than {MAX_ARTIFACT_STEPS} steps.
"""
        if name_hint:
            prompt += f"\nPreferred name: {name_hint}\n"
        if self._llm_call:
            raw = self._llm_call(prompt)
        else:
            from distr.core.workflow_engine.code_generator import CodeGeneratorService

            raw = CodeGeneratorService()._call_coding_llm(prompt)
        cleaned = str(raw or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        payload = json.loads(cleaned)
        if name_hint:
            payload["name"] = name_hint
        payload["source_request"] = request
        payload["request_hash"] = _request_hash(request)

        from distr.core.agent.tools.loader import get_cached_tool

        checked = validate_artifact(payload)
        for step in checked["steps"]:
            if step["kind"] == "tool" and get_cached_tool(step["tool"]) is None:
                raise ValueError(f"Generated artifact references unavailable tool '{step['tool']}'")
        return self.store.save(checked), False


class BuildToolInput(BaseModel):
    request: str = Field(description="The missing capability or reusable multi-step task to build")
    name_hint: str = Field(default="", description="Optional short tool name")
    force_rebuild: bool = Field(default=False, description="Build a new immutable version even when an identical request already exists")


class BuildToolTool(BaseTool):
    """Meta-tool that compiles and registers a deterministic capability."""

    name: str = "build_tool"
    description: str = (
        "Build a missing reusable capability or multi-step action sequence, validate it, freeze it, "
        "and expose it as a callable tool. Use when no existing tool completes the request, or when "
        "several dependent actions should become one systematic repeatable capability."
    )
    args_schema: type[BaseModel] = BuildToolInput

    def __init__(
        self,
        chat_manager: Any = None,
        on_tool_built: Callable[[ArtifactTool], None] | None = None,
        compiler: ToolArtifactCompiler | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_chat_manager", chat_manager)
        object.__setattr__(self, "_on_tool_built", on_tool_built)
        object.__setattr__(self, "_compiler", compiler or ToolArtifactCompiler())

    def _run(self, request: str, name_hint: str = "", force_rebuild: bool = False, **kwargs: Any) -> str:
        chat_id = _current_chat_id(self._chat_manager)
        event_id = None
        if chat_id:
            try:
                from distr.core.agent.tool_audit import record_tool_start

                event_id = record_tool_start(
                    chat_id,
                    "tool_compiler",
                    instruction_hint="Building a missing capability",
                    routing_path="deterministic_tool_builder",
                )
            except Exception:
                pass
        try:
            artifact, reused = self._compiler.compile(
                request,
                name_hint=name_hint,
                force_rebuild=force_rebuild,
            )
            tool = ArtifactTool(artifact, chat_manager=self._chat_manager)
            from distr.core.agent.tools.loader import register_runtime_tool

            register_runtime_tool(tool, source=f"artifact:{artifact['name']}", replace=True)
            if self._on_tool_built:
                self._on_tool_built(tool)
            verb = "Reused" if reused else "Built"
            message = (
                f"{verb} deterministic capability '{tool.name}' version {artifact.get('version', '')}. "
                f"It has {len(artifact['steps'])} visible step(s) and is ready to call."
            )
            if event_id:
                from distr.core.chat_turns import finish_tool

                finish_tool(event_id, success=True, summary=message, detail=message)
            return message
        except Exception as exc:
            message = f"Error building deterministic capability: {exc}"
            if event_id:
                try:
                    from distr.core.chat_turns import finish_tool

                    finish_tool(event_id, success=False, summary=message, detail=message)
                except Exception:
                    pass
            logger.error("build_tool failed", exc_info=True)
            return message

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)
