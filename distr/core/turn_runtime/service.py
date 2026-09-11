"""Runtime selection and active-turn lifecycle management."""

from __future__ import annotations

import os
import threading
import logging
from dataclasses import dataclass, replace
from pathlib import Path

from distr.core.turn_runtime.cli_harness import CliHarnessTurnRuntime
from distr.core.turn_runtime.contracts import (
    CancellationToken,
    SteeringChannel,
    TurnEventSink,
    TurnEvent,
    TurnEventKind,
    TurnRequest,
    TurnResult,
    TurnRuntime,
    TurnCancelled,
    TurnStatus,
)


@dataclass
class _ActiveTurn:
    token: CancellationToken
    steering: SteeringChannel
    runtime_id: str
    turn_id: int | None
    runtime: TurnRuntime | None = None


_active_turns: dict[int, _ActiveTurn] = {}
_active_lock = threading.Lock()
logger = logging.getLogger(__name__)


def _create_native_execution_session(request: TurnRequest) -> int | None:
    if request.metadata.get("source") != "development":
        return None
    try:
        from distr.core.kanban.project_execution import create_execution_session

        return create_execution_session(
            project_id=int(request.scope.project_id),
            ticket_id=request.scope.ticket_id,
            route_type="native_turn",
            route_backend=request.route.provider or "native",
            selected_model=request.route.model,
            selection_reason="Direct Decisions Development turn",
            complexity=request.complexity,
            origin="development",
            input_packet={
                "chat_id": request.scope.chat_id,
                "turn_id": request.scope.turn_id,
                "board_id": request.scope.board_id,
                "prompt": request.prompt,
                "project_folder": request.scope.project_folder,
                "autonomy_level": request.autonomy_level,
                "required_capabilities": list(request.required_capabilities),
            },
        )
    except Exception:
        logger.debug("Could not create native execution session", exc_info=True)
        return None


def _record_native_certification(request: TurnRequest, result: TurnResult, session_id: int | None) -> None:
    try:
        from distr.core.project_cli_backends.policy_manager import _counts_as_model_health_failure
        from distr.core.qualification import ProviderCertificationStore, record_provider_execution

        error = str(result.error or "")
        record_provider_execution(
            ProviderCertificationStore(),
            provider=request.route.provider,
            model=request.route.model,
            capabilities=request.required_capabilities,
            success=bool(result.success),
            route_failure=bool(not result.success and _counts_as_model_health_failure(error or result.output)),
            evidence={
                "execution_session_id": session_id,
                "runtime_id": "native",
                "chat_id": request.scope.chat_id,
                "ticket_id": request.scope.ticket_id,
                "success": bool(result.success),
                "error": error[:1000],
            },
        )
    except Exception:
        logger.debug("Could not record native provider certification", exc_info=True)


def native_turn_available(request: TurnRequest) -> bool:
    from distr.core.turn_runtime.model_registry import supports_native_provider

    return bool(
        supports_native_provider(request.route.provider)
        and request.route.model
        and request.route.model != "auto"
        and request.scope.project_folder
        and Path(request.scope.project_folder).expanduser().is_dir()
    )


def select_turn_runtime_id(request: TurnRequest, runtime_id: str = "") -> str:
    requested = str(
        runtime_id
        or request.metadata.get("runtime_id")
        or os.environ.get("DECISIONSAI_DEVELOPMENT_TURN_RUNTIME")
        or "native"
    ).strip().lower()
    if requested == "native" and native_turn_available(request):
        return "native"
    if requested in {"native", "cli", "cli_harness", "legacy"}:
        return "cli_harness"
    raise ValueError(f"Unknown Decisions turn runtime: {requested}")


def get_turn_runtime(runtime_id: str = "", *, request: TurnRequest | None = None) -> TurnRuntime:
    selected = str(runtime_id or "cli_harness").strip().lower()
    if selected in {"cli", "cli_harness", "legacy"}:
        return CliHarnessTurnRuntime()
    if selected == "native":
        if request is None or not native_turn_available(request):
            raise ValueError("The native Development runtime is not available for this provider, model, or project.")
        from distr.core.settings import load_settings_from_db
        from distr.core.turn_runtime.native import NativeTurnRuntime
        from distr.core.turn_runtime.model_registry import create_native_model_adapter
        from distr.core.turn_runtime.project_tools import ProjectToolExecutor

        return NativeTurnRuntime(
            model=create_native_model_adapter(
                provider=request.route.provider,
                model=request.route.model,
                settings=load_settings_from_db(),
            ),
            tools=ProjectToolExecutor(
                request.scope.project_folder,
                read_only=request.autonomy_level == "plan",
                attachments=list(request.metadata.get("attachments") or []),
                automation_id=str(request.metadata.get("automation_id") or ""),
                automation_thread_id=request.scope.chat_id,
                automation_turn_id=request.scope.turn_id,
                untrusted_external=bool(request.metadata.get("untrusted_external_input")),
            ),
        )
    raise ValueError(f"Unknown Decisions turn runtime: {runtime_id}")


def _configured_provider_fallback(request: TurnRequest) -> TurnRequest | None:
    """Build the one configured Auto fallback without changing manual routes."""
    options = dict(request.route.adapter_options)
    backend = str(options.get("fallback_backend") or "").strip().lower()
    model = str(options.get("fallback_model") or "").strip()
    if not backend or not model:
        return None
    provider = str(options.get("fallback_model_provider") or "").strip().lower()
    if (backend, model, provider) == (
        str(request.route.backend_id or "").strip().lower(),
        str(request.route.model or "").strip(),
        str(request.route.provider or "").strip().lower(),
    ):
        return None
    for key in ("fallback_backend", "fallback_model", "fallback_model_provider"):
        options.pop(key, None)
    return replace(
        request,
        route=replace(
            request.route,
            backend_id=backend,
            model=model,
            provider=provider,
            adapter_options=options,
        ),
        metadata={
            **request.metadata,
            "auto_fallback_from": {
                "backend": request.route.backend_id,
                "provider": request.route.provider,
                "model": request.route.model,
            },
        },
    )


async def execute_turn(
    request: TurnRequest,
    *,
    on_event: TurnEventSink | None = None,
    runtime_id: str = "",
) -> TurnResult:
    token = CancellationToken()
    chat_id = int(request.scope.chat_id)
    selected = select_turn_runtime_id(request, runtime_id)

    def mark_applied(event_ids: tuple[str, ...]) -> None:
        from distr.core.chat_turns import mark_steering_applied

        mark_steering_applied(event_ids)

    steering = SteeringChannel(on_applied=mark_applied)
    active = _ActiveTurn(
        token=token,
        steering=steering,
        runtime_id=selected,
        turn_id=request.scope.turn_id,
    )
    with _active_lock:
        _active_turns[chat_id] = active
    execution_session_id: int | None = None
    native_mutation_started = False
    try:
        try:
            runtime = get_turn_runtime(selected, request=request)
        except (ImportError, ValueError):
            if selected != "native":
                raise
            selected = "cli_harness"
            active.runtime_id = selected
            runtime = get_turn_runtime(selected, request=request)
        active.runtime = runtime
        if selected == "native":
            execution_session_id = _create_native_execution_session(request)

        def relay(event):
            nonlocal native_mutation_started
            if event.kind == TurnEventKind.TOOL_STARTED and event.tool_name in {
                "replace_text",
                "write_file",
                "run_command",
            }:
                native_mutation_started = True
            projected = replace(event, execution_session_id=execution_session_id) if execution_session_id else event
            if execution_session_id and event.kind.value != "output_delta":
                try:
                    from distr.core.kanban.project_execution import append_execution_event

                    append_execution_event(
                        execution_session_id,
                        event.kind.value,
                        status=event.status.value,
                        message=event.summary,
                        payload={
                            "runtime_id": event.runtime_id,
                            "tool_name": event.tool_name,
                            "artifacts": list(event.artifacts),
                            "details": event.details,
                        },
                        update_session_status=False,
                    )
                except Exception:
                    logger.debug("Could not persist native execution event", exc_info=True)
            if on_event:
                on_event(projected)

        try:
            result = await runtime.execute(
                request,
                on_event=relay,
                cancellation=token,
                steering=steering,
            )
        except TurnCancelled:
            raise
        except Exception as native_exc:
            should_fallback = False
            if selected == "native" and not native_mutation_started:
                try:
                    from distr.core.project_cli_backends.policy_manager import _counts_as_model_health_failure

                    should_fallback = _counts_as_model_health_failure(str(native_exc))
                except Exception:
                    should_fallback = False
            if not should_fallback:
                raise
            if execution_session_id:
                from distr.core.kanban.project_execution import complete_execution_session

                complete_execution_session(
                    execution_session_id,
                    success=False,
                    output_packet={"runtime_id": "native", "fallback": "cli_harness"},
                    error=str(native_exc),
                )
                _record_native_certification(
                    request,
                    TurnResult(
                        success=False,
                        runtime_id="native",
                        backend_id="native",
                        model=request.route.model,
                        error=str(native_exc),
                    ),
                    execution_session_id,
                )
                execution_session_id = None
            active.runtime_id = "cli_harness"
            if on_event:
                on_event(
                    TurnEvent(
                        kind=TurnEventKind.STATUS,
                        status=TurnStatus.INITIALIZING,
                        summary="The native provider is unavailable. Continuing with the configured CLI fallback.",
                        runtime_id="cli_harness",
                        execution_session_id=0,
                        details={"fallback_from": "native"},
                    )
                )
            fallback = get_turn_runtime("cli_harness", request=request)
            return await fallback.execute(
                request,
                on_event=on_event,
                cancellation=token,
                steering=steering,
            )
        if not result.success and not native_mutation_started:
            try:
                from distr.core.project_cli_backends.policy_manager import _counts_as_model_health_failure

                route_failed = _counts_as_model_health_failure(result.error or result.output)
            except Exception:
                route_failed = False
            fallback_request = _configured_provider_fallback(request) if route_failed else None
            if fallback_request is not None:
                if execution_session_id:
                    from distr.core.kanban.project_execution import complete_execution_session

                    complete_execution_session(
                        execution_session_id,
                        success=False,
                        output_packet={
                            "runtime_id": selected,
                            "fallback_backend": fallback_request.route.backend_id,
                            "fallback_model": fallback_request.route.model,
                        },
                        error=result.error or result.output,
                    )
                    _record_native_certification(request, result, execution_session_id)
                    execution_session_id = None
                fallback_runtime_id = select_turn_runtime_id(fallback_request)
                active.runtime_id = fallback_runtime_id
                if on_event:
                    source = request.route.provider or request.route.backend_id
                    target = fallback_request.route.provider or fallback_request.route.backend_id
                    on_event(
                        TurnEvent(
                            kind=TurnEventKind.STATUS,
                            status=TurnStatus.INITIALIZING,
                            summary=f"{source} is unavailable. Auto is continuing with {target}.",
                            runtime_id=fallback_runtime_id,
                            execution_session_id=0,
                            details={
                                "fallback_from": {
                                    "backend": request.route.backend_id,
                                    "provider": request.route.provider,
                                    "model": request.route.model,
                                },
                                "fallback_to": {
                                    "backend": fallback_request.route.backend_id,
                                    "provider": fallback_request.route.provider,
                                    "model": fallback_request.route.model,
                                },
                            },
                        )
                    )
                fallback_runtime = get_turn_runtime(fallback_runtime_id, request=fallback_request)
                active.runtime = fallback_runtime
                result = await fallback_runtime.execute(
                    fallback_request,
                    on_event=relay,
                    cancellation=token,
                    steering=steering,
                )
                result = replace(
                    result,
                    evidence={
                        **result.evidence,
                        "auto_fallback": {
                            "from": {
                                "backend": request.route.backend_id,
                                "provider": request.route.provider,
                                "model": request.route.model,
                            },
                            "to": {
                                "backend": fallback_request.route.backend_id,
                                "provider": fallback_request.route.provider,
                                "model": fallback_request.route.model,
                            },
                        },
                    },
                )
        if execution_session_id:
            from distr.core.kanban.project_execution import complete_execution_session

            complete_execution_session(
                execution_session_id,
                success=bool(result.success),
                output_packet={
                    "runtime_id": result.runtime_id,
                    "backend_id": result.backend_id,
                    "model": result.model,
                    "output": result.output,
                    "evidence": result.evidence,
                    "artifacts": list(result.artifacts),
                },
                error=result.error,
            )
            result = replace(result, execution_session_id=execution_session_id)
            _record_native_certification(request, result, execution_session_id)
        return result
    except TurnCancelled:
        if execution_session_id:
            from distr.core.kanban.project_execution import cancel_execution_session

            cancel_execution_session(execution_session_id)
        raise
    except Exception as exc:
        if execution_session_id:
            from distr.core.kanban.project_execution import complete_execution_session

            complete_execution_session(
                execution_session_id,
                success=False,
                output_packet={"runtime_id": "native"},
                error=str(exc),
            )
            _record_native_certification(
                request,
                TurnResult(
                    success=False,
                    runtime_id="native",
                    backend_id="native",
                    model=request.route.model,
                    error=str(exc),
                ),
                execution_session_id,
            )
        raise
    finally:
        with _active_lock:
            if _active_turns.get(chat_id) is active:
                _active_turns.pop(chat_id, None)


def cancel_active_turn(chat_id: int) -> bool:
    with _active_lock:
        active = _active_turns.get(int(chat_id))
    if active is None:
        return False
    active.token.cancel()
    cancel_runtime = getattr(active.runtime, "cancel_active", None)
    if callable(cancel_runtime):
        cancel_runtime()
    return True


def active_turn_registered(chat_id: int) -> bool:
    with _active_lock:
        return int(chat_id) in _active_turns


def steer_active_turn(chat_id: int, guidance: str) -> dict[str, object]:
    """Steer a native direct turn. Other runtimes leave guidance queued."""
    clean = str(guidance or "").strip()
    if not clean:
        raise ValueError("Steering guidance is required.")
    with _active_lock:
        active = _active_turns.get(int(chat_id))
    if active is None or active.runtime_id != "native":
        return {"accepted": False, "reason": "no_active_native_turn"}

    event_id = ""
    if active.turn_id is not None:
        from distr.core.chat_turns import steer_turn

        event = steer_turn(int(chat_id), int(active.turn_id), clean)
        event_id = str((event or {}).get("event_id") or "")
    active.steering.put(clean, event_id=event_id)
    return {
        "accepted": True,
        "method": "native_turn_steering",
        "runtime_id": "native",
        "event_id": event_id,
    }
