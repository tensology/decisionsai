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
    POTENTIALLY_MUTATING_PROJECT_TOOLS,
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
                "project_folder": request.scope.project_folder,
                "autonomy_level": request.autonomy_level,
                "required_capabilities": list(request.required_capabilities),
                "recovery_version": 1,
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
                reasoning_effort=str(request.route.adapter_options.get("reasoning_effort") or ""),
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


def _configured_provider_fallback(request: TurnRequest, error=None) -> TurnRequest | None:
    """Use the route's configured fallback, else the global provider failover order."""
    options = dict(request.route.adapter_options)
    backend = str(options.get("fallback_backend") or "").strip().lower()
    model = str(options.get("fallback_model") or "").strip()
    if not backend or not model:
        return _provider_failover_request(request, error)
    provider = str(options.get("fallback_model_provider") or "").strip().lower()
    if (backend, model, provider) == (
        str(request.route.backend_id or "").strip().lower(),
        str(request.route.model or "").strip(),
        str(request.route.provider or "").strip().lower(),
    ):
        return _provider_failover_request(request, error)
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
            "failover_tried": [
                *[tuple(item) for item in (request.metadata.get("failover_tried") or [])],
                (str(request.route.provider or request.route.backend_id or ""), str(request.route.model or "")),
            ],
            "auto_fallback_from": {
                "backend": request.route.backend_id,
                "provider": request.route.provider,
                "model": request.route.model,
            },
        },
    )


_MAX_FAILOVER_ATTEMPTS = 3


def _route_failed(error) -> bool:
    try:
        from distr.core.provider_failover import is_failover_error

        return is_failover_error(error)
    except Exception:
        return False


def _provider_failover_request(request: TurnRequest, error=None) -> TurnRequest | None:
    """Every route (manual, automation, scheduled) fails over, not only Auto."""
    try:
        from distr.core.provider_failover import log_failover, next_failover
    except Exception:
        return None
    tried = [tuple(item) for item in (request.metadata.get("failover_tried") or [])]
    provider = str(request.route.provider or request.route.backend_id or "")
    target = next_failover(provider, str(request.route.model or ""), tried=tried, error=error)
    log_failover("development", provider, str(request.route.model or ""), error, target)
    if target is None:
        return None
    options = dict(request.route.adapter_options)
    options["model_provider"] = target.provider
    for key in ("fallback_backend", "fallback_model", "fallback_model_provider"):
        options.pop(key, None)
    return replace(
        request,
        route=replace(request.route, provider=target.provider, model=target.model, adapter_options=options),
        metadata={
            **request.metadata,
            "failover_tried": [*tried, (provider, str(request.route.model or ""))],
            "auto_fallback_from": {
                "backend": request.route.backend_id,
                "provider": request.route.provider,
                "model": request.route.model,
            },
        },
    )


def _emit_failover_notice(request, fallback_request, error, on_event, runtime_id) -> None:
    if not on_event:
        return
    try:
        from distr.core.provider_failover import FailoverTarget, provider_down_notice

        summary = provider_down_notice(
            str(request.route.provider or request.route.backend_id or ""),
            str(request.route.model or ""),
            error,
            FailoverTarget(str(fallback_request.route.provider or fallback_request.route.backend_id), str(fallback_request.route.model)),
        )
    except Exception:
        summary = "The model provider is unavailable. Continuing with the next configured provider."
    on_event(
        TurnEvent(
            kind=TurnEventKind.STATUS,
            status=TurnStatus.INITIALIZING,
            summary=summary,
            runtime_id=runtime_id,
            execution_session_id=0,
            details={
                "provider_down_notice": True,
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


def _with_provider_down_notice(request: TurnRequest, result: TurnResult, target, on_event) -> TurnResult:
    """No keyed fallback left: replace the raw provider dump with one clear notice."""
    try:
        from distr.core.provider_failover import provider_down_notice

        raw = str(result.error or result.output or "")
        notice = provider_down_notice(str(request.route.provider or request.route.backend_id or ""), str(request.route.model or ""), raw, target)
    except Exception:
        return result
    return replace(result, error=notice, evidence={**result.evidence, "provider_error": raw[:1000], "provider_down_notice": notice})


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
    native_output_started = False
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
            from distr.core.turn_runtime.recovery import RecoveryJournal

            resume_session_id = request.metadata.get("resume_session_id")
            completed = RecoveryJournal.load(
                resume_session_id, scope=request.scope, turn_id=request.metadata.get("resume_turn_id"),
            ) if resume_session_id else {}
            execution_session_id = _create_native_execution_session(request)
            if resume_session_id and not execution_session_id:
                raise RuntimeError("Could not persist a new execution session for the resumed turn.")
            if execution_session_id:
                journal = RecoveryJournal(execution_session_id, request.scope.project_folder, completed)
                journal.seed()
                request = replace(request, metadata={**request.metadata, "recovery_journal": journal})
        elif request.metadata.get("resume_session_id"):
            raise ValueError("The interrupted turn requires a native Development route to resume safely.")

        def relay(event):
            nonlocal native_mutation_started, native_output_started
            if selected == "native":
                if event.kind == TurnEventKind.TOOL_STARTED and event.tool_name in POTENTIALLY_MUTATING_PROJECT_TOOLS:
                    native_mutation_started = True
                if event.kind == TurnEventKind.OUTPUT_DELTA and event.output_delta:
                    native_output_started = True
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
            if selected == "native" and not (native_mutation_started or native_output_started or request.metadata.get("resume_session_id")):
                try:
                    from distr.core.project_cli_backends.policy_manager import _counts_as_model_health_failure

                    should_fallback = _counts_as_model_health_failure(str(native_exc))
                except Exception:
                    should_fallback = False
                should_fallback = should_fallback or _route_failed(str(native_exc))
            if not should_fallback:
                raise
            provider_request = _configured_provider_fallback(request, error=native_exc)
            if provider_request is not None:
                # The provider itself is down (credits, DNS, ...). Re-running the
                # same provider through the CLI harness would fail the same way,
                # so continue on the next configured, keyed provider instead.
                if execution_session_id:
                    from distr.core.kanban.project_execution import complete_execution_session

                    complete_execution_session(
                        execution_session_id,
                        success=False,
                        output_packet={"runtime_id": "native", "fallback_model": provider_request.route.model},
                        error=str(native_exc),
                    )
                    execution_session_id = None
                fallback_runtime_id = select_turn_runtime_id(provider_request)
                active.runtime_id = fallback_runtime_id
                _emit_failover_notice(request, provider_request, native_exc, on_event, fallback_runtime_id)
                fallback_runtime = get_turn_runtime(fallback_runtime_id, request=provider_request)
                active.runtime = fallback_runtime
                return await fallback_runtime.execute(
                    provider_request,
                    on_event=on_event,
                    cancellation=token,
                    steering=steering,
                )
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
        stopped_by_provider = selected == "native" and "finish_reason" in result.evidence
        if not result.success and not (
            native_mutation_started or native_output_started or stopped_by_provider or request.metadata.get("resume_session_id")
        ):
            try:
                from distr.core.project_cli_backends.policy_manager import _counts_as_model_health_failure

                route_failed = _counts_as_model_health_failure(result.error or result.output)
            except Exception:
                route_failed = False
            route_failed = route_failed or _route_failed(result.error or result.output)
            original_request = request
            attempts = 0
            fallback_request = (
                _configured_provider_fallback(request, error=result.error or result.output) if route_failed else None
            )
            if route_failed and fallback_request is None:
                result = _with_provider_down_notice(request, result, None, on_event)
            while fallback_request is not None and attempts < _MAX_FAILOVER_ATTEMPTS:
                attempts += 1
                if execution_session_id:
                    # Keep this run's session open: the fallback's result is the run's
                    # result. Closing it as "failed" here left tickets showing failed
                    # while OpenRouter was doing the work.
                    from distr.core.kanban.project_execution import append_execution_event

                    try:
                        append_execution_event(
                            execution_session_id,
                            event_type="provider_failover",
                            status="running",
                            message=(
                                f"{request.route.provider or request.route.backend_id} failed; continuing on "
                                f"{fallback_request.route.provider or fallback_request.route.backend_id} "
                                f"{fallback_request.route.model}."
                            ),
                            payload={
                                "fallback_backend": fallback_request.route.backend_id,
                                "fallback_model": fallback_request.route.model,
                                "provider_error": str(result.error or result.output or "")[:500],
                            },
                        )
                    except Exception:
                        pass
                    _record_native_certification(request, result, execution_session_id)
                fallback_runtime_id = select_turn_runtime_id(fallback_request)
                active.runtime_id = fallback_runtime_id
                _emit_failover_notice(request, fallback_request, result.error or result.output, on_event, fallback_runtime_id)
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
                                "backend": original_request.route.backend_id,
                                "provider": original_request.route.provider,
                                "model": original_request.route.model,
                            },
                            "to": {
                                "backend": fallback_request.route.backend_id,
                                "provider": fallback_request.route.provider,
                                "model": fallback_request.route.model,
                            },
                        },
                    },
                )
                if result.success or native_mutation_started or native_output_started:
                    break
                if not _route_failed(result.error or result.output):
                    break
                request = fallback_request
                fallback_request = _configured_provider_fallback(request, error=result.error or result.output)
                if fallback_request is None:
                    result = _with_provider_down_notice(request, result, None, on_event)
            request = original_request
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


def cancel_active_turn(chat_id: int, *, expected_turn_id: int | None = None) -> bool:
    with _active_lock:
        active = _active_turns.get(int(chat_id))
        if (active is None or active.token.cancelled
                or (expected_turn_id is not None and active.turn_id != expected_turn_id)):
            return False
        active.token.cancel()
    cancel_runtime = getattr(active.runtime, "cancel_active", None)
    if callable(cancel_runtime):
        cancel_runtime()
    return True


def active_turn_stop_pending(chat_id: int, turn_id: int) -> bool:
    with _active_lock:
        active = _active_turns.get(chat_id)
        return bool(active and active.turn_id == turn_id and active.token.cancelled)


def active_turn_registered(chat_id: int) -> bool:
    with _active_lock:
        return int(chat_id) in _active_turns


def native_steering_available(chat_id: int, turn_id: int) -> bool:
    with _active_lock:
        active = _active_turns.get(chat_id)
        return bool(active and active.runtime_id == "native" and active.turn_id == turn_id and not active.token.cancelled)


def steer_active_turn(chat_id: int, guidance: str, *, expected_turn_id: int | None = None) -> dict[str, object]:
    """Steer a native direct turn. Other runtimes leave guidance queued."""
    clean = str(guidance or "").strip()
    if not clean:
        raise ValueError("Steering guidance is required.")
    with _active_lock:
        active = _active_turns.get(int(chat_id))
    if active is None or active.runtime_id != "native" or active.token.cancelled:
        return {"accepted": False, "reason": "no_active_native_turn"}
    if expected_turn_id is not None and active.turn_id != expected_turn_id:
        return {"accepted": False, "reason": "active_turn_changed"}

    event_id = ""
    if active.turn_id is not None:
        from distr.core.chat_turns import steer_turn

        event = steer_turn(int(chat_id), int(active.turn_id), clean)
        event_id = str((event or {}).get("event_id") or "")
    with _active_lock:
        if _active_turns.get(int(chat_id)) is active and active.runtime_id == "native" and not active.token.cancelled:
            active.steering.put(clean, event_id=event_id)
            return {"accepted": True, "method": "native_turn_steering", "runtime_id": "native", "event_id": event_id}
    if event_id:
        from distr.core.chat_turns import update_event

        update_event(event_id, status="cancelled", title="Guidance not applied",
                     summary="The worker ended before accepting this instruction.", metadata={"discarded": True})
    return {"accepted": False, "reason": "active_turn_changed"}
