"""Register MCP tools in ToolRegistry with prefixed names and bounded concurrency (R6)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from distr.core.agent.tools.registry import ToolRegistry, get_tool_registry
from distr.core.events.bus import EventBus
from distr.core.events.types import MCP_SERVER_CONNECTED, MCP_SERVER_DISCONNECTED, MCP_TOOL_CALLED
from distr.core.mcp.client import MCPClientHub

logger = logging.getLogger(__name__)

MCP_TOOL_REGISTRATION_CAP = 200
TOOL_CALL_TIMEOUT_SEC = 60.0


class MCPNativeToolCollisionError(ValueError):
    """Prefixed MCP tool name collides with a non-MCP registry entry."""


def _sanitize_segment(segment: str) -> str:
    t = re.sub(r"[^a-zA-Z0-9_]+", "_", (segment or "").strip()).strip("_")
    return t if t else "x"


def prefixed_tool_registry_name(server_name: str, tool_name: str) -> str:
    return f"mcp__{_sanitize_segment(server_name)}__{_sanitize_segment(tool_name)}"


def _prop_field(
    alias: str, spec: dict[str, Any], required: bool
) -> tuple[type, Any]:
    desc = ""
    if isinstance(spec.get("description"), str):
        desc = spec["description"][:2000]
    t = spec.get("type", "string")
    if t == "integer":
        ann: type = int
        if required:
            return ann, Field(..., alias=alias, description=desc)
        return ann, Field(default=0, alias=alias, description=desc)
    if t == "number":
        ann = float
        if required:
            return ann, Field(..., alias=alias, description=desc)
        return ann, Field(default=0.0, alias=alias, description=desc)
    if t == "boolean":
        ann = bool
        if required:
            return ann, Field(..., alias=alias, description=desc)
        return ann, Field(default=False, alias=alias, description=desc)
    if t == "array":
        ann = list[Any]
        if required:
            return ann, Field(..., alias=alias, description=desc)
        return ann, Field(default_factory=list, alias=alias, description=desc)
    if t == "object":
        ann = dict[str, Any]
        if required:
            return ann, Field(..., alias=alias, description=desc)
        return ann, Field(default_factory=dict, alias=alias, description=desc)
    ann = str
    if required:
        return ann, Field(..., alias=alias, description=desc)
    return ann, Field(default="", alias=alias, description=desc)


def _json_schema_to_model(model_name: str, schema: dict[str, Any] | None) -> type[BaseModel]:
    schema = schema or {}
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:

        class EmptyMCPArgs(BaseModel):
            model_config = ConfigDict(extra="forbid")

        EmptyMCPArgs.__name__ = model_name[:80]
        return EmptyMCPArgs

    req_keys: set[str] = set()
    for x in schema.get("required") or []:
        if isinstance(x, str):
            req_keys.add(x)

    field_defs: dict[str, Any] = {}
    idx = 0
    for alias, spec in props.items():
        if not isinstance(alias, str):
            continue
        py_name = f"a_{idx}"
        idx += 1
        ann, fld = _prop_field(alias, spec if isinstance(spec, dict) else {}, alias in req_keys)
        field_defs[py_name] = (ann, fld)

    return create_model(
        model_name,
        __config__=ConfigDict(populate_by_name=True),
        **field_defs,
    )


def _format_mcp_result(raw: dict[str, Any]) -> str:
    content = raw.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        return "\n".join(parts) if parts else json.dumps(raw, ensure_ascii=False)
    return json.dumps(raw, ensure_ascii=False)


class _MCPLangChainTool(BaseTool):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    def __init__(
        self,
        *,
        registry_name: str,
        description: str,
        args_schema: type[BaseModel],
        hub: MCPClientHub,
        server_name: str,
        mcp_tool_name: str,
        bus: EventBus | None,
        timeout_sec: float,
    ) -> None:
        super().__init__(
            name=registry_name, description=description, args_schema=args_schema
        )
        object.__setattr__(self, "_hub", hub)
        object.__setattr__(self, "_server_name", server_name)
        object.__setattr__(self, "_mcp_tool_name", mcp_tool_name)
        object.__setattr__(self, "_bus", bus)
        object.__setattr__(self, "_timeout_sec", timeout_sec)

    def _run(self, **kwargs: Any) -> str:
        validated = self.args_schema.model_validate(kwargs)
        arguments = validated.model_dump(by_alias=True, exclude_unset=True)

        def _call() -> dict[str, Any]:
            return self._hub.call_tool(self._server_name, self._mcp_tool_name, arguments)

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_call)
                raw = fut.result(timeout=self._timeout_sec)
        except FuturesTimeoutError:
            return (
                f"MCP tool {self._mcp_tool_name!r} timed out after "
                f"{int(self._timeout_sec)}s"
            )
        except Exception as e:
            logger.warning(
                "MCP tool call failed server=%s tool=%s: %s",
                self._server_name,
                self._mcp_tool_name,
                e,
                exc_info=True,
            )
            return f"MCP tool error ({self._mcp_tool_name}): {e}"

        if self._bus is not None:
            payload = json.dumps(arguments, sort_keys=True, default=str).encode()
            digest = hashlib.sha256(payload).hexdigest()
            self._bus.publish(
                MCP_TOOL_CALLED,
                {
                    "server": self._server_name,
                    "tool": self._mcp_tool_name,
                    "args_sha256": digest,
                },
            )
        if not isinstance(raw, dict):
            return str(raw)
        return _format_mcp_result(raw)


class MCPToolAdapter:
    """Publish MCP tools into :class:`ToolRegistry` (R6)."""

    def __init__(
        self,
        hub: MCPClientHub,
        registry: ToolRegistry | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._hub = hub
        self._registry = registry or get_tool_registry()
        self._bus = bus
        self._lock = threading.Lock()
        self._handlers_attached = False
        self._on_connected = self._handle_bus_reconcile
        self._on_disconnected = self._handle_bus_reconcile

    def attach(self) -> None:
        if self._bus is None:
            from distr.core.events.bus import get_event_bus

            self._bus = get_event_bus()
        if self._handlers_attached:
            return
        assert self._bus is not None
        self._bus.subscribe(MCP_SERVER_CONNECTED, self._on_connected)
        self._bus.subscribe(MCP_SERVER_DISCONNECTED, self._on_disconnected)
        self._handlers_attached = True

    def detach(self) -> None:
        if not self._handlers_attached or self._bus is None:
            return
        self._bus.unsubscribe(MCP_SERVER_CONNECTED, self._on_connected)
        self._bus.unsubscribe(MCP_SERVER_DISCONNECTED, self._on_disconnected)
        self._handlers_attached = False

    def _handle_bus_reconcile(self, _event_type: str, _data: Any) -> None:
        try:
            self.reconcile()
        except Exception:
            logger.exception("MCPToolAdapter reconcile failed")

    def reconcile(self) -> int:
        """Drop MCP registrations and re-register from currently connected servers."""
        with self._lock:
            self._unregister_all_mcp_sources()
            entries: list[tuple[str, str, dict[str, Any]]] = []
            for server_name in self._hub.enabled_connected_servers():
                try:
                    tools = self._hub.list_tools(server_name)
                except Exception:
                    logger.warning(
                        "MCP adapter: list_tools failed for %r",
                        server_name,
                        exc_info=True,
                    )
                    continue
                for spec in tools:
                    if not isinstance(spec, dict):
                        continue
                    tn = spec.get("name")
                    if not isinstance(tn, str) or not tn.strip():
                        continue
                    entries.append((server_name, tn.strip(), spec))
            entries.sort(key=lambda x: (x[0], x[1]))
            total_before_cap = len(entries)
            if len(entries) > MCP_TOOL_REGISTRATION_CAP:
                logger.warning(
                    "MCP tool cap: registering %s of %s tools (server, name order)",
                    MCP_TOOL_REGISTRATION_CAP,
                    len(entries),
                )
                entries = entries[:MCP_TOOL_REGISTRATION_CAP]

            seen_registry_names: set[str] = set()
            registered = 0
            for server_name, tool_name, spec in entries:
                reg_name = prefixed_tool_registry_name(server_name, tool_name)
                if reg_name in seen_registry_names:
                    raise ValueError(
                        f"MCP registry name collision after sanitization: {reg_name!r}"
                    )
                seen_registry_names.add(reg_name)

                rec = self._registry.get_record(reg_name)
                if rec is not None and not rec.source.startswith("mcp:"):
                    raise MCPNativeToolCollisionError(
                        f"MCP tool name {reg_name!r} conflicts with existing "
                        f"tool from source {rec.source!r}"
                    )

                desc = (
                    spec["description"]
                    if isinstance(spec.get("description"), str)
                    else ""
                )
                desc = (desc or f"MCP tool {tool_name} on server {server_name}").strip()
                schema_raw = spec.get("inputSchema")
                schema_dict = schema_raw if isinstance(schema_raw, dict) else {}
                model_name = (
                    f"MCPArgs_{hashlib.sha256(reg_name.encode()).hexdigest()[:16]}"
                )
                args_model = _json_schema_to_model(model_name, schema_dict)

                tool = _MCPLangChainTool(
                    registry_name=reg_name,
                    description=desc[:8000],
                    args_schema=args_model,
                    hub=self._hub,
                    server_name=server_name,
                    mcp_tool_name=tool_name,
                    bus=self._bus,
                    timeout_sec=TOOL_CALL_TIMEOUT_SEC,
                )
                try:
                    self._registry.register(tool, f"mcp:{server_name}")
                except ValueError as e:
                    raise MCPNativeToolCollisionError(str(e)) from e
                registered += 1

            if total_before_cap > MCP_TOOL_REGISTRATION_CAP:
                logger.info(
                    "MCP adapter: omitted %s tools over cap",
                    total_before_cap - MCP_TOOL_REGISTRATION_CAP,
                )

            self._refresh_tool_index()
            return registered

    def _unregister_all_mcp_sources(self) -> None:
        records = self._registry.iter_records()
        sources = sorted({r.source for r in records if r.source.startswith("mcp:")})
        for src in sources:
            self._registry.unregister_by_source(src)

    def _refresh_tool_index(self) -> None:
        try:
            from distr.core.agent.tools.loader import get_warmed_tools_list
            from distr.core.agent.tool_retriever import schedule_tool_index_rebuild

            schedule_tool_index_rebuild(get_warmed_tools_list())
        except Exception:
            logger.debug("tool index rebuild skipped", exc_info=True)
