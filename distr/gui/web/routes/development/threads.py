"""Threads HTTP boundary. Existing URLs remain compatible."""
from fastapi import File, Form, UploadFile
from fastapi.responses import JSONResponse
from typing import Optional
import os
import asyncio
import uuid
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
from .models import StudioRoutingAssessmentRequest, StudioSkillCaptureRequest, StudioTaskCreateRequest, StudioThreadArchiveRequest, StudioThreadForkRequest, StudioThreadMessageRequest, StudioThreadUpdateRequest


def register_routes(router, templates):
    @router.post("/workflows/studio/attachments")
    async def upload_studio_attachment(file: UploadFile = File(...), project_id: Optional[int] = Form(None)):
        """Store a bounded chat attachment where the local development harness can read it."""
        filename = Path(str(file.filename or "attachment")).name
        suffix = Path(filename).suffix[:16]
        max_mb = max(1, int(os.environ.get("DECISIONS_STUDIO_ATTACHMENT_MAX_MB", "512")))
        max_bytes = max_mb * 1024 * 1024
        scope = str(int(project_id)) if project_id else "inbox"
        root = Path.home() / ".decisions" / "workspaces" / "projects" / scope / "attachments"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = root / f"{uuid.uuid4().hex}{suffix}"
        size = 0
        try:
            with destination.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        output.close()
                        destination.unlink(missing_ok=True)
                        return JSONResponse({"detail": f"Attachment exceeds the {max_mb} MB limit."}, status_code=413)
                    output.write(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return JSONResponse({
            "name": filename,
            "path": str(destination),
            "mime_type": str(file.content_type or "application/octet-stream"),
            "size": size,
        })


    @router.post("/workflows/studio/routing-assessment")
    async def workflow_studio_routing_assessment(data: StudioRoutingAssessmentRequest):
        """Assess operational complexity and failure signals before an automatic route is chosen."""
        from distr.core.kanban.ticket_policy import _global_complexity_route, infer_ticket_complexity

        instruction = str(data.instruction or "").strip()
        combined = "\n".join(
            [instruction, data.ticket_title or "", data.ticket_description or ""]
            + [str(item or "") for item in (data.recent_messages or [])[-6:]]
        ).lower()
        complexity = infer_ticket_complexity(
            data.ticket_title or instruction[:160],
            f"{data.ticket_description}\n{instruction}",
            file_count=1 if data.has_images else 0,
        )
        blocked_terms = ("blocked", "stuck", "cannot continue", "permission denied", "unavailable")
        failure_terms = ("failed", "failing", "regression", "exception", "traceback", "broken", "error")
        risk_terms = ("security", "production", "data loss", "migration", "payment", "credential")
        signals = []
        if any(term in combined for term in blocked_terms):
            signals.append("blocked")
        if any(term in combined for term in failure_terms):
            signals.append("failure")
        if any(term in combined for term in risk_terms):
            signals.append("risk")
        if data.has_images:
            signals.append("vision")
        operational_state = "blocked" if "blocked" in signals else "failing" if "failure" in signals else "risk" if "risk" in signals else "neutral"
        if complexity == "low" and signals:
            complexity = "medium"
        if complexity == "medium" and ("risk" in signals or {"blocked", "failure"}.issubset(signals)):
            complexity = "high"
        route = dict(_global_complexity_route(complexity))
        if data.has_images and route.get("model") not in {"", "auto"}:
            route["model"] = "auto"
        from distr.core.workflow.execution_mode import choose_development_execution_mode

        execution_mode = choose_development_execution_mode(
            instruction,
            assessment={
                "complexity": complexity,
                "operational_state": operational_state,
                "signals": signals,
            },
        )
        return JSONResponse({
            "complexity": complexity,
            "operational_state": operational_state,
            "sentiment": operational_state,
            "signals": signals,
            "route": route,
            "execution_mode": execution_mode["mode"],
            "execution_mode_reason": execution_mode["reason"],
            "execution_mode_signals": execution_mode["signals"],
            "reason": f"{complexity.title()} complexity" + (f" with {', '.join(signals)} signals" if signals else " based on the instruction scope"),
        })


    @router.post("/workflows/studio/tasks")
    async def workflow_studio_create_task(data: StudioTaskCreateRequest):
        """Create one durable Development thread and dispatch its IDE agent."""
        prompt = str(data.prompt or "").strip()
        if not prompt:
            return JSONResponse({"detail": "A task prompt is required."}, status_code=422)
        route_mode = str(data.route_mode or "auto").strip().lower()
        autonomy_level = str(data.autonomy_level or "full").strip().lower()
        execution_profile = str(data.execution_profile or "code").strip().lower()
        if route_mode not in {"auto", "manual"}:
            return JSONResponse({"detail": "route_mode must be auto or manual"}, status_code=422)
        if autonomy_level not in {"full", "approval", "plan", "goal"}:
            return JSONResponse({"detail": "Unknown autonomy level"}, status_code=422)
        if execution_profile not in {"code", "design", "research", "operations"}:
            return JSONResponse({"detail": "Unknown execution profile"}, status_code=422)
        try:
            from distr.core.skills.catalog import filter_known_skill_ids
            from distr.core.chat import ChatService
            from distr.core.db import Chat, get_session
            from distr.core.db.workflow import AutoWorkflow
            from distr.core.db.kanban import KanbanTicket
            from distr.core.db.projects import Project
            from distr.core.settings import load_settings_from_db

            with get_session() as db:
                if data.project_id is not None:
                    if db.get(Project, int(data.project_id)) is None:
                        return JSONResponse({"detail": "Selected project does not exist."}, status_code=422)
                if data.workflow_id is not None:
                    if db.get(AutoWorkflow, int(data.workflow_id)) is None:
                        return JSONResponse({"detail": "Selected workflow does not exist."}, status_code=422)
                if data.ticket_id is not None:
                    if db.get(KanbanTicket, int(data.ticket_id)) is None:
                        return JSONResponse({"detail": "Selected ticket does not exist."}, status_code=422)

            settings = load_settings_from_db()
            provider = str(
                data.provider
                or settings.get("conversational_llm_provider")
                or settings.get("agent_provider")
                or "ollama"
            ).strip()
            model_name = str(
                data.model_name
                or settings.get("conversational_llm_model")
                or settings.get("agent_model")
                or ""
            ).strip()
            title = str(data.title or data.board_ticket_title or "").strip() or None
            ticket_id = data.ticket_id
            skill_ids = filter_known_skill_ids(list(data.skill_ids or []))[:12]
            chat_id, _ = await asyncio.to_thread(
                ChatService.create_new_chat,
                llm_provider=provider,
                llm_model=model_name,
                title=title,
                starting_question=prompt,
                project_id=data.project_id,
                route_mode=route_mode,
                execution_profile=execution_profile,
                autonomy_level=autonomy_level,
                starting_metadata={"skill_ids": skill_ids} if skill_ids else None,
                activate=False,
            )
            from distr.core.workflow.development_threads import mark_development_thread

            await asyncio.to_thread(
                mark_development_thread,
                int(chat_id),
                workflow_id=data.workflow_id,
                ticket_id=ticket_id,
                board_key=data.board_key,
                board_provider=data.board_provider,
                board_ticket_key=data.board_ticket_key,
                board_ticket_title=data.board_ticket_title,
                board_ticket_lane=data.board_ticket_lane,
            )
            if ticket_id is None and str(data.board_key or "").startswith("decisions:"):
                from distr.core.workflow.development_threads import rebind_development_thread

                binding = await asyncio.to_thread(
                    rebind_development_thread,
                    int(chat_id),
                    title=title,
                    project_id=data.project_id,
                    board_key=data.board_key,
                    board_provider=data.board_provider or "local",
                    ticket_id=None,
                    board_ticket_key=None,
                    board_ticket_title=title or prompt[:120],
                    board_ticket_lane=None,
                )
                ticket_id = binding.get("ticket_id")
            from distr.core.workflow.development_control import update_thread_controls

            permission_mode = str(data.permission_mode or "standard").strip().lower()
            if permission_mode not in {"standard", "careful", "trusted"}:
                permission_mode = "standard"
            if autonomy_level in {"approval", "plan"} and permission_mode == "standard":
                permission_mode = "careful"
            await asyncio.to_thread(
                update_thread_controls,
                int(chat_id),
                permission_profile={"mode": permission_mode, "persisted": True},
                remote_continuation=False,
            )
            from distr.core.workflow.development_control import resume_thread_time

            await asyncio.to_thread(resume_thread_time, int(chat_id))

            from distr.core.workflow.development_threads import update_development_model_route

            await asyncio.to_thread(
                update_development_model_route,
                int(chat_id),
                route_mode=route_mode,
                provider=provider if route_mode == "manual" else None,
                model_name=model_name if route_mode == "manual" else None,
                reasoning_effort=str(data.reasoning_effort or "medium").strip().lower(),
                service_tier=str(data.service_tier or "standard").strip().lower(),
            )
            if ticket_id is not None:
                with get_session() as db:
                    ticket = db.get(KanbanTicket, int(ticket_id))
                    if ticket is None:
                        return JSONResponse({"detail": "Selected ticket does not exist."}, status_code=422)
                    ticket.linked_project_id = data.project_id or ticket.linked_project_id
                    ticket.source_chat_id = int(chat_id)
                    db.commit()

            execution = None
            should_start = autonomy_level != "approval"
            if should_start:
                if data.workflow_id is not None:
                    from distr.core.workflow.work_dispatch import dispatch_work_item

                    execution = await asyncio.to_thread(
                        dispatch_work_item,
                        workflow_id=int(data.workflow_id),
                        chat_id=int(chat_id),
                        context=prompt,
                        project_id=data.project_id,
                        ticket_id=ticket_id,
                        source_type="studio_thread",
                        run_metadata={"skill_ids": skill_ids, "routing_assessment": data.routing_assessment or {}},
                        dispatch_async=True,
                    )
                else:
                    from distr.core.workflow.development_harness import dispatch_development_prompt

                    execution = await asyncio.to_thread(
                        dispatch_development_prompt,
                        int(chat_id),
                        prompt,
                        routing_assessment=data.routing_assessment or {},
                        attachments=data.attachments or [],
                        skill_ids=skill_ids,
                        use_playwright=bool(data.use_playwright),
                        dispatch_async=True,
                    )
                if isinstance(execution, dict) and execution.get("error"):
                    return JSONResponse(
                        {
                            "chat_id": chat_id,
                            "detail": execution.get("error"),
                            "next_action": "Open the thread and choose another provider or project folder before retrying.",
                        },
                        status_code=409,
                    )

            with get_session() as db:
                chat = db.get(Chat, int(chat_id))
                chat_payload = {
                    "id": int(chat.id),
                    "title": chat.title or "New task",
                    "project_id": chat.project_id,
                    "provider": chat.provider,
                    "model_name": chat.model_name,
                    "route_mode": chat.route_mode or "auto",
                    "execution_profile": chat.execution_profile or "code",
                    "autonomy_level": chat.autonomy_level or "full",
                    "created_date": chat.created_date.isoformat() if chat.created_date else None,
                    "modified_date": chat.modified_date.isoformat() if chat.modified_date else None,
                }
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
            return JSONResponse(
                {
                    "task": chat_payload,
                    "workflow": {"id": int(data.workflow_id)} if data.workflow_id is not None else None,
                    "turn": execution,
                    "execution": execution,
                    "artifacts": [],
                    "plan_revision": None,
                    "started": should_start,
                    "message": (
                        "Thread created."
                        if should_start
                        else "Thread created and waiting for approval."
                    ),
                }
            )
        except Exception as e:
            logger.error("Studio task creation failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/messages")
    async def workflow_studio_message(chat_id: int, data: StudioThreadMessageRequest):
        """Route a Development message directly to its persistent IDE harness."""
        message = str(data.message or "").strip()
        if not message:
            return JSONResponse({"detail": "A development instruction is required."}, status_code=422)
        try:
            from distr.core.skills.catalog import filter_known_skill_ids
            from distr.core.chat import ChatService
            from distr.core.db import Chat, get_session
            from distr.core.workflow.development_threads import development_thread_metadata

            with get_session() as db:
                chat = db.get(Chat, int(chat_id))
                if chat is None or chat.parent_id is not None:
                    return JSONResponse({"detail": "Development thread not found."}, status_code=404)
                metadata = development_thread_metadata(chat)
                if not metadata:
                    return JSONResponse({"detail": "This conversation is not a development thread."}, status_code=409)
                autonomy_level = str(chat.autonomy_level or "full").strip().lower()
                project_id = int(chat.project_id) if chat.project_id is not None else None
            skill_ids = filter_known_skill_ids(list(data.skill_ids or []))[:12]
            await asyncio.to_thread(
                ChatService.add_user_message,
                int(chat_id),
                message,
                metadata={"skill_ids": skill_ids} if skill_ids else None,
            )
            from distr.core.workflow.development_control import resume_thread_time

            await asyncio.to_thread(resume_thread_time, int(chat_id))

            if autonomy_level == "approval":
                return JSONResponse(
                    {
                        "success": True,
                        "execution": None,
                        "started": False,
                        "message": "Instruction saved and waiting for approval.",
                    }
                )

            workflow_id = int(metadata.get("workflow_id") or 0) or None
            if workflow_id is not None:
                from distr.core.workflow.work_dispatch import dispatch_work_item

                execution = await asyncio.to_thread(
                    dispatch_work_item,
                    workflow_id=workflow_id,
                    chat_id=int(chat_id),
                    context=message,
                    project_id=project_id,
                    ticket_id=metadata.get("ticket_id"),
                    source_type="studio_thread",
                    run_metadata={"skill_ids": skill_ids, "routing_assessment": data.routing_assessment or {}},
                    dispatch_async=True,
                )
            else:
                from distr.core.workflow.development_harness import dispatch_development_prompt

                execution = await asyncio.to_thread(
                    dispatch_development_prompt,
                    int(chat_id),
                    message,
                    routing_assessment=data.routing_assessment or {},
                    attachments=data.attachments or [],
                    skill_ids=skill_ids,
                    use_playwright=bool(data.use_playwright),
                    dispatch_async=True,
                )
            if isinstance(execution, dict) and execution.get("error"):
                await asyncio.to_thread(
                    ChatService.append_assistant_notice,
                    int(chat_id),
                    f"Execution could not start: {execution['error']}",
                )
                return JSONResponse({"detail": execution["error"]}, status_code=409)
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
            return JSONResponse({"success": True, "execution": execution, "turn": execution, "started": True})
        except Exception as exc:
            logger.error("Studio development message failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.get("/workflows/studio/tasks/{chat_id}/execution")
    async def workflow_studio_execution(chat_id: int):
        try:
            from distr.core.workflow.development_harness import development_execution_state

            return JSONResponse(await asyncio.to_thread(development_execution_state, int(chat_id)))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception as exc:
            logger.error("Studio execution state failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/execution/stop")
    async def workflow_studio_stop_execution(chat_id: int):
        try:
            from distr.core.workflow.development_harness import stop_development_execution

            return JSONResponse(await asyncio.to_thread(stop_development_execution, int(chat_id)))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception as exc:
            logger.error("Studio execution stop failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.get("/workflows/studio/tasks/{chat_id}/execution/changes")
    async def workflow_studio_execution_changes(chat_id: int):
        try:
            from distr.core.workflow.development_harness import development_change_review

            return JSONResponse(await asyncio.to_thread(development_change_review, int(chat_id)))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception as exc:
            logger.error("Studio change review failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/execution/changes/undo")
    async def workflow_studio_undo_execution_changes(chat_id: int):
        try:
            from distr.core.workflow.development_harness import undo_development_changes

            return JSONResponse(await asyncio.to_thread(undo_development_changes, int(chat_id)))
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except Exception as exc:
            logger.error("Studio change undo failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/clear-context")
    async def workflow_studio_clear_context(chat_id: int):
        try:
            from distr.core.workflow.development_control import clear_thread_context

            return JSONResponse(await asyncio.to_thread(clear_thread_context, chat_id))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)


    @router.post("/workflows/studio/tasks/{chat_id}/fork")
    async def workflow_studio_fork_thread(chat_id: int, data: StudioThreadForkRequest):
        try:
            from distr.core.workflow.development_control import fork_thread

            return JSONResponse(
                await asyncio.to_thread(fork_thread, chat_id, title=data.title)
            )
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except Exception as e:
            logger.error("Studio fork failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.delete("/workflows/studio/tasks/{chat_id}")
    async def workflow_studio_delete_thread(
        chat_id: int,
        delete_linked_ticket: bool = True,
    ):
        try:
            from distr.core.workflow.development_control import delete_thread

            return JSONResponse(
                await asyncio.to_thread(
                    delete_thread,
                    chat_id,
                    delete_linked_ticket=delete_linked_ticket,
                )
            )
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except Exception as e:
            logger.error("Studio delete failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.patch("/workflows/studio/tasks/{chat_id}")
    async def workflow_studio_update_thread(chat_id: int, data: StudioThreadUpdateRequest):
        """Rename a thread or explicitly update its ticket relationship."""
        try:
            from distr.core.workflow.development_threads import rebind_development_thread, update_development_thread_settings

            scope_fields = {"project_id", "board_key", "board_provider", "ticket_id", "board_ticket_key", "board_ticket_title", "board_ticket_lane"}
            if scope_fields.intersection(data.model_fields_set):
                result = await asyncio.to_thread(
                    rebind_development_thread,
                    chat_id,
                    title=data.title,
                    project_id=data.project_id,
                    board_key=data.board_key,
                    board_provider=data.board_provider,
                    ticket_id=data.ticket_id,
                    board_ticket_key=data.board_ticket_key,
                    board_ticket_title=data.board_ticket_title,
                    board_ticket_lane=data.board_ticket_lane,
                    permission_profile=data.permission_profile,
                    remote_continuation=data.remote_continuation,
                )
            else:
                result = await asyncio.to_thread(
                    update_development_thread_settings,
                    chat_id,
                    title=data.title,
                    permission_profile=data.permission_profile,
                    remote_continuation=data.remote_continuation,
                )
            return JSONResponse({
                **result,
                "status": "updated",
                "summary": "Development thread and work scope saved.",
            })
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except Exception as e:
            logger.error("Studio thread update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/archive")
    async def workflow_studio_archive_thread(chat_id: int, data: StudioThreadArchiveRequest):
        try:
            from distr.core.workflow.development_control import archive_thread

            return JSONResponse(await asyncio.to_thread(archive_thread, chat_id, archived=data.archived))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except Exception as e:
            logger.error("Studio archive failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/tasks/{chat_id}/export")
    async def workflow_studio_export_thread(chat_id: int):
        try:
            from distr.core.workflow.development_control import thread_export

            return JSONResponse(await asyncio.to_thread(thread_export, chat_id, redacted=True))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except Exception as e:
            logger.error("Studio export failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/snapshots")
    async def workflow_studio_create_snapshot(chat_id: int):
        try:
            from distr.core.workflow.development_control import create_shared_snapshot

            return JSONResponse(await asyncio.to_thread(create_shared_snapshot, chat_id), status_code=201)
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except Exception as e:
            logger.error("Studio snapshot failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/shared/{token}")
    async def workflow_studio_shared_snapshot(token: str):
        try:
            from distr.core.workflow.development_control import get_shared_snapshot

            snapshot = await asyncio.to_thread(get_shared_snapshot, token)
            if snapshot is None:
                return JSONResponse({"detail": "Snapshot not found."}, status_code=404)
            return JSONResponse(snapshot)
        except Exception as e:
            logger.error("Studio shared snapshot failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/skills")
    async def workflow_studio_capture_skill(chat_id: int, data: StudioSkillCaptureRequest):
        try:
            from distr.core.workflow.development_control import capture_execution_skill

            return JSONResponse(
                await asyncio.to_thread(
                    capture_execution_skill,
                    chat_id,
                    data.execution_session_id,
                    name=data.name,
                ),
                status_code=201,
            )
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except Exception as e:
            logger.error("Studio skill capture failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


