"""Project terminal HTTP and WebSocket boundary."""
from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect
from typing import Any
import asyncio
import json
import os
import shutil
import logging

logger = logging.getLogger(__name__)

from distr.core.terminals.service import _attach_process_usage, _backend_id_for_project, _coerce_optional_int, _discover_project_server_processes, _managed_process_tree_pids, _resolve_terminal_overview_llm, _stop_discovered_project_process

def register_routes(router, templates):
    @router.get("/projects/terminal-status")
    async def get_project_terminal_statuses(project_ids: str = ""):
        """Return lightweight managed-terminal state for responsive web/tray sync."""
        from distr.core.terminal import get_startup_sessions_for_project

        ids: list[int] = []
        for raw_id in project_ids.split(","):
            project_id = _coerce_optional_int(raw_id.strip())
            if project_id and project_id not in ids:
                ids.append(project_id)
            if len(ids) >= 200:
                break

        projects: dict[str, dict[str, Any]] = {}
        for project_id in ids:
            startup = _attach_process_usage(
                get_startup_sessions_for_project(project_id, purpose="startup")
            )
            shell = _attach_process_usage(
                get_startup_sessions_for_project(project_id, purpose="cli_shell")
            )
            projects[str(project_id)] = {
                "running": bool(startup or shell),
                "startup_count": len(startup),
                "shell_count": len(shell),
                "sessions": startup + shell,
            }
        return JSONResponse({"projects": projects})

    @router.websocket("/projects/{project_id}/terminal/ws")
    async def terminal_websocket(websocket: WebSocket, project_id: int):
        """WebSocket for real-time pi RPC transcript. Connects to a pi --mode rpc session."""
        from distr.core.pi_rpc import get_or_create_rpc_session, kill_rpc_session
        from distr.core.project_cli_backends.live_sessions import (
            any_live_session_running,
            clear_live_session_buffer,
            live_session_is_alive,
            set_live_session_connected,
            publish_live_session_event,
            register_live_session_listener,
            set_live_session_running,
            snapshot_live_session,
            unregister_live_session_listener,
        )
        from distr.gui.web.security import websocket_has_valid_internal_token, is_allowed_local_origin

        # Auth check
        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        if not websocket_has_valid_internal_token(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return

        await websocket.accept()
        websocket_board_id = _coerce_optional_int(websocket.query_params.get("board_id"))
        websocket_workflow_id = _coerce_optional_int(websocket.query_params.get("workflow_id"))

        # Get project folder
        try:
            from distr.core.db import get_session
            from distr.core.db.projects import Project
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    await websocket.send_json({"type": "error", "message": "Project not found"})
                    await websocket.close(code=1008, reason="Project not found")
                    return
                cwd = project.folder_location or os.path.expanduser("~")
                project_name = project.name or ""
                backend_id = _backend_id_for_project(project)
                backend_model = (project.coding_backend_model or "").strip()
        except Exception as e:
            logger.error(f"Terminal: failed to load project: {e}")
            await websocket.send_json({"type": "error", "message": "Failed to load project"})
            await websocket.close(code=1011, reason="Internal error")
            return

        # Ensure the directory exists
        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        from distr.core.project_cli_backends import get_backend, run_project_task
        backend = get_backend(backend_id)
        loop = asyncio.get_running_loop()

        if not backend.supports_rpc:
            status = backend.setup_status()
            event_queue: asyncio.Queue = asyncio.Queue()
            register_live_session_listener(project_id, backend.id, event_queue, board_id=websocket_board_id)
            await websocket.send_json({
                "type": "connected",
                "project_id": project_id,
                "backend": backend.id,
                "board_id": websocket_board_id,
                "workflow_id": websocket_workflow_id,
                "supports_rpc": False,
                "alive": live_session_is_alive(project_id, backend.id, board_id=websocket_board_id),
                "buffer": snapshot_live_session(project_id, backend.id, board_id=websocket_board_id),
            })
            if not status.ready:
                await websocket.send_json({
                    "type": "error",
                    "message": f"{backend.name} is not ready: {status.message} {status.setup_instructions}".strip(),
                })

            async def _send_event(event_dict):
                try:
                    await websocket.send_json(event_dict)
                except Exception:
                    pass

            async def _forward_events():
                while True:
                    event = await event_queue.get()
                    await _send_event(event)

            forward_task = asyncio.create_task(_forward_events())

            try:
                while True:
                    data = await websocket.receive_text()
                    try:
                        msg = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    msg_type = msg.get("type")
                    if msg_type == "prompt":
                        instruction = (msg.get("message") or "").strip()
                        if not instruction:
                            continue
                        model_override = (msg.get("model") or msg.get("coding_backend_model") or "").strip() or None
                        codex_reasoning_effort = (msg.get("codex_reasoning_effort") or "").strip() or None
                        codex_service_tier = (msg.get("codex_service_tier") or "").strip() or None
                        setup = backend.setup_status()
                        if not setup.ready:
                            await websocket.send_json({
                                "type": "error",
                                "message": (setup.message or setup.setup_instructions or f"{backend.name} is not ready.").strip(),
                            })
                            continue
                        if live_session_is_alive(project_id, backend.id, board_id=websocket_board_id):
                            await websocket.send_json({"type": "error", "message": f"{backend.name} is still running. Wait for it to finish before sending another task."})
                            continue
                        if any_live_session_running(project_id, board_id=websocket_board_id):
                            await websocket.send_json({"type": "error", "message": "Another workflow CLI is already processing work for this board. Wait for it to finish before starting a different backend."})
                            continue

                        async def _run_one():
                            set_live_session_running(project_id, backend.id, True, board_id=websocket_board_id)

                            def _queue_event(event_dict):
                                try:
                                    loop.call_soon_threadsafe(
                                        lambda: publish_live_session_event(
                                            project_id,
                                            backend.id,
                                            event_dict,
                                            board_id=websocket_board_id,
                                        )
                                    )
                                except Exception:
                                    pass

                            try:
                                from types import SimpleNamespace
                                p = SimpleNamespace(
                                    id=project_id,
                                    name=project_name,
                                    folder_location=cwd,
                                    coding_backend=backend.id,
                                    coding_backend_model=model_override or backend_model,
                                )
                                result = await run_project_task(
                                    p,
                                    instruction,
                                    on_event=_queue_event,
                                    origin="cli",
                                    workflow_id=websocket_workflow_id,
                                    model_override=model_override,
                                    codex_reasoning_effort_override=codex_reasoning_effort,
                                    codex_service_tier_override=codex_service_tier,
                                    board_id_override=websocket_board_id,
                                )
                                if not result.success:
                                    _queue_event({"type": "error", "message": result.error or f"{backend.name} failed"})
                                else:
                                    set_live_session_connected(
                                        project_id,
                                        backend.id,
                                        True,
                                        board_id=websocket_board_id,
                                        external_session_id="latest",
                                    )
                            finally:
                                set_live_session_running(project_id, backend.id, False, board_id=websocket_board_id)

                        asyncio.create_task(_run_one())
                    elif msg_type == "abort":
                        from distr.core.project_cli_backends.registry import abort_backend_process
                        cancelled = await abort_backend_process(project_id, backend.id, board_id=websocket_board_id)
                        if cancelled:
                            publish_live_session_event(project_id, backend.id, {"type": "error", "message": f"{backend.name} task cancelled."}, board_id=websocket_board_id)
                        else:
                            publish_live_session_event(project_id, backend.id, {"type": "error", "message": f"{backend.name} was not running."}, board_id=websocket_board_id)
                    elif msg_type == "restart":
                        if not live_session_is_alive(project_id, backend.id, board_id=websocket_board_id):
                            clear_live_session_buffer(project_id, backend.id, board_id=websocket_board_id)
                        await websocket.send_json({
                            "type": "connected",
                            "project_id": project_id,
                            "backend": backend.id,
                            "board_id": websocket_board_id,
                            "workflow_id": websocket_workflow_id,
                            "supports_rpc": False,
                            "alive": live_session_is_alive(project_id, backend.id, board_id=websocket_board_id),
                            "buffer": snapshot_live_session(project_id, backend.id, board_id=websocket_board_id),
                        })
                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.debug(f"Generic CLI terminal WebSocket error: {e}")
            finally:
                unregister_live_session_listener(project_id, backend.id, event_queue, board_id=websocket_board_id)
                forward_task.cancel()
            return

        # Create or get the pi RPC session (lazy: don't auto-start pi until first prompt)
        try:
            rpc = await get_or_create_rpc_session(project_id, cwd, lazy_start=True, board_id=websocket_board_id)
        except Exception as e:
            logger.error(f"Terminal: failed to create pi RPC session: {e}")
            await websocket.send_json({"type": "error", "message": f"Failed to start pi: {e}"})
            await websocket.close(code=1011, reason="Terminal error")
            return

        # Queue for RPC events to be sent to this WebSocket
        event_queue = asyncio.Queue()

        # The RPC reader runs in a background thread, so we need a thread-safe
        # way to push events into the asyncio queue.
        loop = asyncio.get_event_loop()

        def _on_event(event_dict):
            try:
                loop.call_soon_threadsafe(event_queue.put_nowait, event_dict)
            except Exception:
                pass

        rpc.add_event_callback(_on_event)

        # Send initial connection message + existing transcript
        buffer_messages = rpc.get_messages()
        await websocket.send_json({"type": "connected", "project_id": project_id, "backend": backend.id, "board_id": websocket_board_id, "workflow_id": websocket_workflow_id, "supports_rpc": True, "alive": bool(getattr(rpc, "is_alive", False)), "buffer": buffer_messages})

        from distr.core.pi_preflight import preflight_pi_coding_cli

        pf = preflight_pi_coding_cli(project_id=project_id, cwd=cwd, probe_model=True)
        await websocket.send_json({"type": "preflight", **pf.to_dict()})

        async def _forward_events():
            """Forward RPC events to WebSocket client."""
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=30)
                    await websocket.send_json(event)
                except asyncio.TimeoutError:
                    # Send keepalive
                    try:
                        await websocket.send_json({"type": "ping"})
                    except Exception:
                        break
                except Exception:
                    break

        # Start event forwarding task
        forward_task = asyncio.create_task(_forward_events())

        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type")

                if msg_type == "prompt":
                    # User sent a prompt to pi via the CLI input
                    instruction = msg.get("message", "")
                    if instruction:
                        pf_prompt = preflight_pi_coding_cli(project_id=project_id, cwd=cwd, probe_model=True)
                        if not pf_prompt.ok:
                            await websocket.send_json({
                                "type": "error",
                                "message": pf_prompt.user_message,
                                "preflight": pf_prompt.to_dict(),
                            })
                            continue
                        # send_prompt auto-starts pi if lazy-started (first prompt)
                        if not rpc.send_prompt(instruction, origin="cli"):
                            err = getattr(rpc, "_last_preflight_error", None) or "Failed to send prompt — pi may not be available"
                            await websocket.send_json({"type": "error", "message": err})
                elif msg_type == "steer":
                    # User is steering/redirecting pi
                    instruction = msg.get("message", "")
                    if instruction:
                        rpc.steer(instruction)
                elif msg_type == "abort":
                    # User wants to abort current operation
                    rpc.abort()
                elif msg_type == "restart":
                    # Kill and restart pi RPC session
                    await kill_rpc_session(project_id, board_id=websocket_board_id)
                    try:
                        rpc = await get_or_create_rpc_session(project_id, cwd, lazy_start=True, board_id=websocket_board_id)
                        rpc.add_event_callback(_on_event)
                        await websocket.send_json({"type": "connected", "project_id": project_id, "backend": backend.id, "board_id": websocket_board_id, "workflow_id": websocket_workflow_id, "supports_rpc": True, "alive": bool(getattr(rpc, "is_alive", False)), "buffer": rpc.get_messages()})
                    except Exception as e:
                        await websocket.send_json({"type": "error", "message": f"Failed to restart: {e}"})
                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"Terminal WebSocket error: {e}")
        finally:
            forward_task.cancel()
            rpc.remove_event_callback(_on_event)

    @router.get("/projects/{project_id}/terminal/buffer")
    async def get_terminal_buffer(project_id: int, lines: int = 100, board_id: int | None = None):
        """Get terminal buffer content from the selected backend when available."""
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends.live_sessions import (
            live_session_is_alive,
            live_session_connected,
            live_session_external_id,
            live_session_should_expire,
            set_live_session_connected,
            replay_buffer_text,
            snapshot_live_session,
            snapshot_live_session_meta,
        )
        from distr.core.project_cli_backends import get_backend
        from distr.core.pi_rpc import get_rpc_session

        board_id = _coerce_optional_int(board_id)
        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"buffer": "", "alive": False, "project_id": project_id, "backend_id": "", "supports_rpc": False})
            backend_id = _backend_id_for_project(project)
            backend = get_backend(backend_id)
            folder = project.folder_location or os.path.expanduser("~")

        alive = False
        connected = False
        external_thread_id = ""
        live_meta = snapshot_live_session_meta(project_id, backend_id, board_id=board_id)
        if backend.supports_rpc:
            rpc = get_rpc_session(project_id, board_id=board_id)
            alive = bool(rpc and getattr(rpc, "is_alive", False))
            connected = alive
            buffer_text = rpc.get_buffer(lines) if rpc else backend.get_buffer(project_id, lines)
        else:
            if live_session_should_expire(project_id, backend_id, board_id=board_id):
                try:
                    await backend.disconnect_session(project_id, folder)
                except Exception:
                    pass
                set_live_session_connected(project_id, backend_id, False, board_id=board_id, external_session_id="")
                live_meta = snapshot_live_session_meta(project_id, backend_id, board_id=board_id)
            alive = live_session_is_alive(project_id, backend_id, board_id=board_id)
            connected = live_session_connected(project_id, backend_id, board_id=board_id)
            external_thread_id = live_session_external_id(project_id, backend_id, board_id=board_id)
            buffer_text = replay_buffer_text(snapshot_live_session(project_id, backend_id, board_id=board_id))
        if not buffer_text and not alive:
            return JSONResponse({
                "buffer": "",
                "alive": False,
                "connected": connected,
                "external_thread_id": external_thread_id,
                "project_id": project_id,
                "backend_id": backend_id,
                "supports_rpc": bool(backend.supports_rpc),
                **live_meta,
            })

        return JSONResponse({
            "buffer": buffer_text,
            "alive": alive,
            "connected": connected,
            "external_thread_id": external_thread_id,
            "project_id": project_id,
            "backend_id": backend_id,
            "supports_rpc": bool(backend.supports_rpc),
            **live_meta,
        })

    @router.post("/projects/{project_id}/terminal/keepalive")
    async def keep_terminal_session_alive(project_id: int, request: Request):
        from distr.core.project_cli_backends.live_sessions import mark_live_session_presence, snapshot_live_session_meta

        try:
            body = await request.json()
        except ClientDisconnect:
            # Navigating away can cancel the browser's keepalive/sendBeacon
            # request mid-body. That is an expected lifecycle event, not an
            # application fault worth a full ASGI exception traceback.
            return JSONResponse({"success": False, "disconnected": True}, status_code=499)
        backend_id = str(body.get("backend_id") or "").strip() or "pi"
        workflow_id = _coerce_optional_int(body.get("workflow_id"))
        board_id = _coerce_optional_int(body.get("board_id"))
        present = bool(body.get("present", True))
        mark_live_session_presence(
            project_id,
            backend_id,
            workflow_id=workflow_id,
            board_id=board_id,
            present=present,
        )
        return JSONResponse({
            "success": True,
            "project_id": project_id,
            "backend_id": backend_id,
            **snapshot_live_session_meta(project_id, backend_id, board_id=board_id),
        })

    @router.post("/projects/{project_id}/cli-session/disconnect")
    async def disconnect_project_cli_session(project_id: int, request: Request):
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend, normalize_backend_id
        from distr.core.project_cli_backends.live_sessions import mark_live_session_presence, set_live_session_connected
        from distr.core.project_cli_backends.registry import abort_backend_process

        body = await request.json()
        backend_id = normalize_backend_id(body.get("backend_id") or "")
        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"success": False, "error": "Project not found"}, status_code=404)
            folder = project.folder_location or os.path.expanduser("~")
        backend = get_backend(backend_id)
        board_id = _coerce_optional_int(body.get("board_id"))
        aborted = await abort_backend_process(project_id, backend_id, board_id=board_id)
        try:
            await backend.disconnect_session(project_id, folder)
        except Exception:
            pass
        mark_live_session_presence(project_id, backend_id, workflow_id=None, board_id=board_id, present=False)
        set_live_session_connected(project_id, backend_id, False, board_id=board_id, external_session_id="")
        return JSONResponse({
            "success": True,
            "backend_id": backend_id,
            "board_id": board_id,
            "aborted": bool(aborted),
            "message": f"{backend.name} session disconnected.",
        })

    @router.post("/projects/{project_id}/terminal/restart")
    async def restart_terminal(project_id: int):
        """Kill and recreate the pi RPC session for a project."""
        from distr.core.pi_rpc import kill_rpc_session, get_or_create_rpc_session
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project

        try:
            with db_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if not project:
                    raise HTTPException(status_code=404, detail="Project not found")
                cwd = project.folder_location or os.path.expanduser("~")
                backend_id = _backend_id_for_project(project)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")

        from distr.core.project_cli_backends import get_backend
        backend = get_backend(backend_id)
        if not backend.supports_rpc:
            result = await backend.restart(project_id, cwd)
            return JSONResponse(result.to_dict() | {"success": result.success})

        await kill_rpc_session(project_id)
        try:
            rpc = await get_or_create_rpc_session(project_id, cwd)
            return JSONResponse({"success": True, "alive": rpc.is_alive})
        except Exception as e:
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

    @router.get("/projects/{project_id}/startup-sessions")
    async def list_startup_sessions(project_id: int):
        """Return all alive startup PTY sessions for a project.
        Used by the frontend to reconnect after a page reload.
        Automatically cleans up dead sessions from the registry."""
        from distr.core.terminal import get_startup_sessions_for_project, materialize_queued_startup_terminals
        try:
            started, failed = await materialize_queued_startup_terminals(project_id)
            if started or failed:
                logger.info("Materialized queued startup sessions for project %s: started=%s failed=%s", project_id, started, failed)
        except Exception as e:
            logger.warning("Failed to materialize queued startup sessions for project %s: %s", project_id, e)
        sessions = _attach_process_usage(get_startup_sessions_for_project(project_id, purpose="startup"))
        try:
            from distr.core.db import get_session as db_session
            from distr.core.db.projects import Project

            with db_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                folder = (project.folder_location or "").strip() if project else ""
            discovered = _discover_project_server_processes(
                folder,
                excluded_pids=_managed_process_tree_pids({int(row.get("pid") or 0) for row in sessions}),
            )
        except Exception as e:
            logger.debug("Project process discovery failed for %s: %s", project_id, e)
            discovered = []
        return JSONResponse({"sessions": sessions + discovered})

    @router.post("/projects/{project_id}/startup-terminals/start")
    async def start_project_startup_terminals_api(project_id: int, request: Request):
        """Start all startup terminals for a project and speak a short confirmation."""
        from distr.core.project_startup_terminals import start_project_startup_terminals_async
        from distr.core.terminal import get_startup_sessions_for_project

        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                body = await request.json()
            except Exception:
                body = {}
        commands = body.get("commands")
        if isinstance(commands, list):
            commands = [str(command).strip() for command in commands if str(command).strip()]
        else:
            commands = None
        startup_instructions = body.get("startup_instructions")
        if startup_instructions is not None:
            startup_instructions = str(startup_instructions)
        start_time_tracker = body.get("start_time_tracker") if "start_time_tracker" in body else None
        if start_time_tracker is not None:
            start_time_tracker = bool(start_time_tracker)

        result = await start_project_startup_terminals_async(
            project_id,
            commands=commands,
            announce=True,
            startup_instructions=startup_instructions,
            start_time_tracker=start_time_tracker,
        )
        sessions = _attach_process_usage(get_startup_sessions_for_project(project_id, purpose="startup"))
        return JSONResponse({
            "success": result.success,
            "message": result.message,
            "speak_message": result.speak_message,
            "action": result.action,
            "started": result.started,
            "failed": result.failed,
            "sessions": sessions,
        })

    @router.post("/projects/{project_id}/startup-terminals/stop")
    async def stop_project_startup_terminals_api(project_id: int):
        """Stop all startup terminals for a project and speak a short confirmation."""
        from distr.core.project_startup_terminals import stop_project_startup_terminals

        result = stop_project_startup_terminals(project_id, announce=True)
        return JSONResponse({
            "success": result.success,
            "message": result.message,
            "speak_message": result.speak_message,
            "action": result.action,
            "stopped": result.stopped,
        })

    @router.get("/projects/{project_id}/shell-terminal")
    async def get_project_shell_terminal(project_id: int):
        """Return alive interactive shell terminal sessions for this project."""
        from distr.core.terminal import get_startup_sessions_for_project
        sessions = _attach_process_usage(get_startup_sessions_for_project(project_id, purpose="cli_shell"))
        return JSONResponse({"sessions": sessions})

    @router.post("/projects/{project_id}/shell-terminal/start")
    async def start_project_shell_terminal(project_id: int):
        """Create one interactive shell PTY for the project's root folder."""
        import shutil
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.terminal import create_startup_shell_session, get_startup_sessions_for_project

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"success": False, "error": "Project not found"}, status_code=404)
            folder = (project.folder_location or "").strip()
            if not folder or not os.path.isdir(folder):
                return JSONResponse({"success": False, "error": "Project has no valid folder location"}, status_code=400)
            canonical = os.path.realpath(folder)

        existing = get_startup_sessions_for_project(project_id, purpose="cli_shell")
        if existing:
            return JSONResponse({"success": True, "process_id": existing[0]["process_id"], "pid": existing[0]["pid"], "reused": True})

        user_shell = os.environ.get("SHELL", "").strip()
        shell_name = os.path.basename(user_shell) if user_shell else ""
        if "zsh" in shell_name:
            shell_cmd = "[zsh] exec zsh -il"
        elif "bash" in shell_name:
            shell_cmd = "[bash] exec bash -il"
        else:
            fallback_shell = shutil.which("zsh") or shutil.which("bash") or "/bin/bash"
            if os.path.basename(fallback_shell) == "zsh":
                shell_cmd = "[zsh] exec zsh -il"
            else:
                shell_cmd = "[bash] exec bash -il"

        try:
            terminal_id, sess = await create_startup_shell_session(project_id, canonical, shell_cmd, purpose="cli_shell")
        except Exception as e:
            logger.error(f"shell-terminal spawn failed: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        return JSONResponse({"success": True, "process_id": terminal_id, "pid": sess.pid, "reused": False})

    @router.post("/projects/startup-terminal")
    async def start_startup_terminal(request: Request):
        """Spawn a shell command in a PTY; client opens WebSocket for output."""
        body = await request.json()
        project_id = int(body.get("project_id") or 0)
        command = (body.get("command") or "").strip()
        working_dir = (body.get("working_dir") or "").strip()
        if not project_id or not command:
            return JSONResponse({"success": False, "error": "project_id and command required"}, status_code=400)

        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.terminal import create_startup_shell_session

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"success": False, "error": "Project not found"}, status_code=404)
            folder = (project.folder_location or "").strip()
            if not folder or not os.path.isdir(folder):
                return JSONResponse({"success": False, "error": "Project has no valid folder location"}, status_code=400)
            canonical = os.path.realpath(folder)
            if working_dir:
                try:
                    req = os.path.realpath(os.path.expanduser(working_dir))
                except OSError:
                    return JSONResponse({"success": False, "error": "Invalid working_dir"}, status_code=400)
                if req != canonical:
                    return JSONResponse({"success": False, "error": "working_dir must match the project's folder"}, status_code=400)

        try:
            terminal_id, _sess = await create_startup_shell_session(project_id, canonical, command)
        except Exception as e:
            logger.error(f"startup-terminal spawn failed: {e}", exc_info=True)
            return JSONResponse({"success": False, "error": str(e)}, status_code=500)

        return JSONResponse({
            "success": True,
            "process_id": terminal_id,
            "pid": _sess.pid,
        })

    @router.websocket("/projects/startup-terminal/{terminal_id}/ws")
    async def startup_terminal_websocket(websocket: WebSocket, terminal_id: str):
        from distr.core.terminal import get_startup_session
        from distr.gui.web.security import websocket_has_valid_internal_token, is_allowed_local_origin

        origin = websocket.headers.get("origin")
        if origin and not is_allowed_local_origin(origin):
            await websocket.close(code=1008, reason="Origin not allowed")
            return
        if not websocket_has_valid_internal_token(websocket):
            await websocket.close(code=1008, reason="Unauthorized")
            return

        sess = get_startup_session(terminal_id)
        if not sess:
            await websocket.close(code=1008, reason="Session not found")
            return

        await websocket.accept()

        # Replay buffered output so the reconnected terminal shows previous output
        if sess._raw_buffer:
            try:
                replay = sess._raw_buffer.decode("utf-8", errors="replace")
                await websocket.send_text(
                    json.dumps({"type": "output", "data": replay, "terminal_id": terminal_id})
                )
            except Exception as e:
                logger.debug(f"startup terminal replay failed: {e}")

        sess.add_websocket(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "resize":
                    rows = int(msg.get("rows") or 24)
                    cols = int(msg.get("cols") or 80)
                    sess.resize(max(2, min(rows, 200)), max(20, min(cols, 500)))
                elif msg.get("type") == "input":
                    inp = msg.get("data")
                    if isinstance(inp, str) and inp:
                        sess.write(inp)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug(f"startup terminal ws: {e}")
        finally:
            sess.remove_websocket(websocket)

    @router.post("/projects/kill-terminal")
    async def kill_terminal_process(request: Request):
        body = await request.json()
        process_id = (body.get("process_id") or "").strip()
        if not process_id:
            return JSONResponse({"success": False, "error": "process_id required"}, status_code=400)
        from distr.core.terminal import kill_startup_session, get_startup_session
        sess = get_startup_session(process_id)
        pid = sess.pid if sess else _coerce_optional_int(body.get("pid"))
        if sess:
            ok = await kill_startup_session(process_id)
        elif process_id.startswith("discovered:") and pid and _coerce_optional_int(body.get("project_id")):
            from distr.core.db import get_session as db_session
            from distr.core.db.projects import Project

            project_id = int(body["project_id"])
            with db_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                folder = (project.folder_location or "").strip() if project else ""
            ok = await asyncio.to_thread(_stop_discovered_project_process, pid, folder)
        else:
            ok = False
        logger.info(f"kill-terminal: key={process_id} pid={pid} success={ok}")
        return JSONResponse({"success": ok, "pid": pid})

    @router.post("/projects/{project_id}/terminal/overview")
    async def terminal_overview(project_id: int):
        """Get selected backend transcript, produce a natural spoken summary, and speak it aloud."""
        import asyncio
        from distr.core.settings import load_settings_from_db
        from distr.core.llm_factory import create_stream
        from distr.core.db import get_session as db_session
        from distr.core.db.projects import Project
        from distr.core.project_cli_backends import get_backend

        with db_session() as session:
            project = session.query(Project).filter(Project.id == project_id).first()
            if not project:
                return JSONResponse({"error": "Project not found"}, status_code=404)
            backend = get_backend(_backend_id_for_project(project))

        # Get structured transcript
        messages = backend.get_messages(project_id)
        if not messages:
            return JSONResponse({"summary": "The terminal is empty — nothing has been output yet.", "empty": True})

        # Extract user commands, assistant responses, and tool activity
        user_msgs = []
        assistant_msgs = []
        tool_msgs = []
        for msg in messages:
            role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
            if role == "user":
                content = ((msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")) or "").strip()
                if content:
                    user_msgs.append(content)
            elif role == "assistant":
                content = ((msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")) or "").strip()
                if content:
                    assistant_msgs.append(content)
            elif role == "tool_result":
                tool_name = (msg.get("tool_name", "") if isinstance(msg, dict) else getattr(msg, "tool_name", "")) or "tool"
                tool_result = ((msg.get("tool_result", "") if isinstance(msg, dict) else getattr(msg, "tool_result", "")) or "").strip()
                is_error = (msg.get("is_error", False) if isinstance(msg, dict) else getattr(msg, "is_error", False))
                tool_msgs.append(f"{'ERROR' if is_error else 'OK'} {tool_name}: {tool_result[:200]}")

        if not user_msgs and not assistant_msgs and not tool_msgs:
            return JSONResponse({"summary": "The terminal has no commands yet.", "empty": True})

        # Build a focused transcript for LLM summarization
        # Last commands, responses, and tool calls, truncated for the LLM
        transcript_parts = []
        for cmd in user_msgs[-5:]:
            truncated = cmd[:200] + "..." if len(cmd) > 200 else cmd
            transcript_parts.append(f"[cmd] {truncated}")
        for resp in assistant_msgs[-5:]:
            truncated = resp[:400] + "..." if len(resp) > 400 else resp
            transcript_parts.append(f"[resp] {truncated}")
        for tool_msg in tool_msgs[-5:]:
            transcript_parts.append(f"[tool] {tool_msg}")

        buffer = "\n".join(transcript_parts)
        if len(buffer) > 4000:
            buffer = buffer[-4000:]

        settings = load_settings_from_db()
        provider, model = _resolve_terminal_overview_llm(settings)
        logger.info("Terminal overview: LLM provider=%s model=%s", provider, model)

        # LLM prompt: produce natural spoken language for TTS
        system_prompt = (
            "You produce short TTS-friendly summaries of terminal activity. "
            "Always use this structure in plain spoken English:\n"
            "1) Intent: what the user asked for.\n"
            "2) Actions: what was run or done (high-level, no raw commands).\n"
            "3) Outcome: what was found or achieved.\n"
            "Rules:\n"
            "- Sound natural, as if talking to a colleague.\n"
            "- Never read file paths, directory trees, JSON blobs, or raw command output.\n"
            "- Mention errors only as high-level outcome, not stack traces/details.\n"
            "- Keep it concise (2-4 short sentences total).\n"
            "Examples:\n"
            "- 'You asked to inspect the project setup. I listed the main files and checked the package configuration. The project uses a standard frontend setup with the expected scripts and dependencies.'\n"
            "- 'You asked to verify what happened in the terminal. I reviewed the recent steps and tool checks. The flow completed successfully and produced the expected result.'\n"
        )
        llm_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Terminal transcript:\n" + buffer},
        ]

        # Run LLM in thread pool so it doesn't block uvicorn
        def _summarize():
            try:
                summary_parts = []
                for token in create_stream(provider, model, llm_messages, settings):
                    summary_parts.append(token)
                return "".join(summary_parts).strip()
            except Exception as e:
                logger.error(f"Terminal overview LLM call failed: {e}", exc_info=True)
                return f"Error: {str(e)[:200]}"

        try:
            loop = asyncio.get_running_loop()
            summary = await loop.run_in_executor(None, _summarize)
        except Exception as e:
            logger.error(f"Terminal overview executor failed: {e}", exc_info=True)
            summary = f"Error: {str(e)[:200]}"

        # Speak the summary aloud (web thread -> agent event queue -> Qt main thread)
        try:
            from distr.core.signals import speak_text_directly_event_queue

            logger.info(f"Terminal overview: speaking {len(summary)} chars")
            speak_text_directly_event_queue(summary)
        except Exception as e:
            logger.warning(f"Failed to speak terminal overview: {e}", exc_info=True)

        return JSONResponse({"summary": summary, "empty": False, "buffer_lines": len(messages)})

