"""Compatibility runtime for the existing project CLI harnesses."""

from __future__ import annotations

from typing import Any

from distr.core.turn_runtime.contracts import (
    CancellationToken,
    SteeringChannel,
    TurnEvent,
    TurnEventKind,
    TurnEventSink,
    TurnRequest,
    TurnResult,
    TurnStatus,
)


class CliHarnessTurnRuntime:
    """Adapt Pi, Codex, Claude, and other CLI workers to the Turn Runtime contract."""

    runtime_id = "cli_harness"

    @staticmethod
    def _changed_files(item: dict[str, Any]) -> list[dict[str, Any]]:
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        if not changes and (item.get("path") or item.get("file")):
            changes = [item]
        files: list[dict[str, Any]] = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = str(change.get("path") or change.get("file") or change.get("filename") or "").strip()
            if not path:
                continue
            patch = str(change.get("diff") or change.get("patch") or "")
            additions = change.get("additions")
            deletions = change.get("deletions")
            files.append(
                {
                    "path": path,
                    "additions": (
                        int(additions)
                        if isinstance(additions, int)
                        else sum(1 for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
                    ),
                    "deletions": (
                        int(deletions)
                        if isinstance(deletions, int)
                        else sum(1 for line in patch.splitlines() if line.startswith("-") and not line.startswith("---"))
                    ),
                }
            )
        return files

    @staticmethod
    def _item_tool(item: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        item_type = str(item.get("type") or "").strip().lower()
        details: dict[str, Any] = {
            "call_id": str(item.get("id") or item.get("call_id") or ""),
            "item_type": item_type,
        }
        if item_type == "command_execution":
            details["command"] = str(item.get("command") or "")
            return "run_command", "Command", details
        if item_type in {"file_change", "file_edit", "patch"}:
            details["files"] = CliHarnessTurnRuntime._changed_files(item)
            return "apply_patch", "File edit", details
        if item_type in {"web_search", "browser", "browser_check"}:
            return "browser_check", "Browser check", details
        if item_type in {"mcp_tool_call", "tool_call"}:
            tool = str(item.get("tool") or item.get("name") or "mcp_tool")
            server = str(item.get("server") or "").strip()
            details["server"] = server
            return tool, tool.replace("_", " ").title(), details
        if "agent" in item_type or "collab" in item_type:
            name = str(item.get("agent_name") or item.get("name") or "Code reviewer")
            details["agent_name"] = name
            return "review_agent", name, details
        title = str(item.get("name") or item_type or "Development action")
        return item_type or "development_action", title.replace("_", " ").title(), details

    async def execute(
        self,
        request: TurnRequest,
        *,
        on_event: TurnEventSink | None = None,
        cancellation: CancellationToken | None = None,
        steering: SteeringChannel | None = None,
    ) -> TurnResult:
        from distr.core.project_cli_backends.harness import HarnessContext, dispatch_harness

        token = cancellation or CancellationToken()
        token.raise_if_cancelled()

        def relay(raw: dict[str, Any]) -> None:
            token.raise_if_cancelled()
            summary = str(
                raw.get("summary") or raw.get("message") or raw.get("type") or "Working"
            )[:1000]
            raw_type = str(raw.get("type") or "").lower()
            assistant_event = raw.get("assistantMessageEvent")
            if (
                raw_type == "message_update"
                and isinstance(assistant_event, dict)
                and assistant_event.get("type") == "text_delta"
            ):
                delta = str(assistant_event.get("delta") or "")
                if on_event and delta:
                    on_event(
                        TurnEvent(
                            kind=TurnEventKind.OUTPUT_DELTA,
                            status=TurnStatus.WORKING,
                            summary="Writing an update.",
                            runtime_id=self.runtime_id,
                            output_delta=delta,
                            execution_session_id=(
                                int(raw["execution_session_id"])
                                if raw.get("execution_session_id")
                                else None
                            ),
                        )
                    )
                return
            if raw_type in {"item.started", "item.completed"} and isinstance(raw.get("item"), dict):
                item = dict(raw["item"])
                item_type = str(item.get("type") or "").lower()
                if item_type == "agent_message":
                    text = str(item.get("text") or "")
                    if on_event and raw_type == "item.completed" and text:
                        on_event(
                            TurnEvent(
                                kind=TurnEventKind.OUTPUT_DELTA,
                                status=TurnStatus.WORKING,
                                summary="Writing an update.",
                                runtime_id=self.runtime_id,
                                output_delta=text,
                                execution_session_id=(
                                    int(raw["execution_session_id"])
                                    if raw.get("execution_session_id")
                                    else None
                                ),
                            )
                        )
                    return
                if item_type == "reasoning":
                    summary = str(item.get("text") or item.get("summary") or "Thinking through the request.")[:1000]
                    raw_type = "status"
                else:
                    tool_name, title, details = self._item_tool(item)
                    failed = str(item.get("status") or "").lower() in {
                        "failed",
                        "error",
                    } or bool(item.get("error"))
                    details.update(
                        {
                            "title": title,
                            "status": (
                                "error" if failed else str(item.get("status") or "success")
                            ),
                        }
                    )
                    item_summary = str(
                        item.get("summary")
                        or item.get("aggregated_output")
                        or item.get("result")
                        or title
                    ).strip()[:1000]
                    if on_event:
                        on_event(
                            TurnEvent(
                                kind=(
                                    TurnEventKind.TOOL_STARTED
                                    if raw_type == "item.started"
                                    else TurnEventKind.TOOL_COMPLETED
                                ),
                                status=(
                                    TurnStatus.WORKING
                                    if raw_type == "item.started"
                                    else TurnStatus.FAILED
                                    if failed
                                    else TurnStatus.UPDATING
                                ),
                                summary=item_summary,
                                runtime_id=self.runtime_id,
                                tool_name=tool_name,
                                execution_session_id=(
                                    int(raw["execution_session_id"])
                                    if raw.get("execution_session_id")
                                    else None
                                ),
                                details=details,
                            )
                        )
                    return
            if raw_type in {"agent_start", "agent_end"}:
                backend = str(raw.get("backend") or request.route.backend_id or "Development").strip()
                role = str(request.route.adapter_options.get("step_role") or "").strip().lower()
                title = "Code reviewer" if role in {"review", "validation"} else f"{backend.title()} worker"
                details = {
                    "call_id": f"worker:{backend}",
                    "title": title,
                    "agent_name": title,
                    "status": "success",
                }
                if on_event:
                    on_event(
                        TurnEvent(
                            kind=(
                                TurnEventKind.TOOL_STARTED
                                if raw_type == "agent_start"
                                else TurnEventKind.TOOL_COMPLETED
                            ),
                            status=(
                                TurnStatus.WORKING
                                if raw_type == "agent_start"
                                else TurnStatus.UPDATING
                            ),
                            summary=(
                                f"{title} "
                                f"{'started' if raw_type == 'agent_start' else 'finished'}."
                            ),
                            runtime_id=self.runtime_id,
                            tool_name=(
                                "review_agent"
                                if role in {"review", "validation"}
                                else "development_agent"
                            ),
                            execution_session_id=(
                                int(raw["execution_session_id"])
                                if raw.get("execution_session_id")
                                else None
                            ),
                            details=details,
                        )
                    )
                return
            command = raw.get("command") if raw_type == "command_start" else None
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            if on_event:
                on_event(
                    TurnEvent(
                        kind=TurnEventKind.STATUS,
                        status=TurnStatus.WORKING,
                        summary=summary,
                        runtime_id=self.runtime_id,
                        tool_name="",
                        execution_session_id=(
                            int(raw["execution_session_id"])
                            if raw.get("execution_session_id")
                            else None
                        ),
                        details={**dict(raw), **({"command": str(command)} if command else {})},
                    )
                )

        route = request.route
        options = dict(route.adapter_options)
        if route.provider:
            options["model_provider"] = route.provider
        if request.autonomy_level == "plan":
            options["read_only_expected"] = True
        handle = await dispatch_harness(
            HarnessContext(
                project=request.project,
                instruction=request.instruction,
                backend_id=route.backend_id,
                chat_id=request.scope.chat_id,
                model=route.model,
                ticket_id=request.scope.ticket_id,
                board_id=request.scope.board_id,
                ticket_complexity=request.complexity,
                codex_reasoning_effort=str(options.get("reasoning_effort") or ""),
                codex_service_tier=str(options.get("service_tier") or ""),
                origin="development",
                on_event=relay,
                required_capabilities=list(request.required_capabilities),
                adapter_options=options,
            )
        )
        token.raise_if_cancelled()
        result = handle.result
        return TurnResult(
            success=bool(result and result.success),
            runtime_id=self.runtime_id,
            backend_id=route.backend_id,
            model=route.model,
            output=str((result.output if result else "") or ""),
            error=str((result.error if result else "") or ""),
            waits_for_human=bool(result and result.waits_for_human),
            execution_session_id=handle.execution_session_id,
            evidence=dict(getattr(handle, "evidence", {}) or {}),
        )
