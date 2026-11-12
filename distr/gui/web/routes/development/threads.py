"""Threads HTTP boundary. Existing URLs remain compatible."""
from fastapi import Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from typing import Optional
import os
import asyncio
import json
import uuid
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
from .models import StudioCheckpointDecisionRequest, StudioRoutingAssessmentRequest, StudioSkillCaptureRequest, StudioSteeringRequest, StudioTaskCreateRequest, StudioThreadArchiveRequest, StudioThreadForkRequest, StudioThreadMessageRequest, StudioThreadUpdateRequest
from distr.gui.web.security import require_internal_token_dependency


def register_routes(router, templates):
    @router.post("/workflows/studio/tasks/{chat_id}/attachments/{attachment_id}/transcript")
    async def studio_thread_attachment_transcript(chat_id: int, attachment_id: str):
        from fastapi import HTTPException
        from distr.core.incoming.transcription import transcribe_thread_attachment

        try:
            return await asyncio.to_thread(transcribe_thread_attachment, chat_id, attachment_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, 'Transcription could not be completed. Please retry.') from exc

    @router.get("/workflows/studio/tasks/{chat_id}/attachments/{attachment_id}")
    async def studio_thread_attachment(chat_id: int, attachment_id: str):
        from fastapi import HTTPException
        from fastapi.responses import FileResponse
        from distr.core.incoming.media import resolve_thread_attachment

        try:
            path, record = await asyncio.to_thread(resolve_thread_attachment, chat_id, attachment_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        mime = str(record['mime_type']).split(';', 1)[0].strip().lower()
        inline = mime in {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf'} or mime.startswith(('audio/', 'video/'))
        return FileResponse(path, media_type=mime, filename=record['name'],
                            content_disposition_type='inline' if inline else 'attachment',
                            headers={'X-Content-Type-Options': 'nosniff'})

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


    @router.get("/workflows/studio/local-harness-presets")
    async def workflow_studio_local_harness_presets():
        """Return dynamic local Ollama workers from live ollama list /api/tags (no pulls)."""
        from distr.core.workflow.development_harness import available_local_harness_workers

        def _load():
            workers = available_local_harness_workers()
            available_ids = [str(row.get("id") or "") for row in workers if row.get("id")]
            return {
                "presets": workers,  # legacy key
                "workers": workers,
                "catalog": workers,
                "available_ids": available_ids,
            }

        try:
            return JSONResponse(await asyncio.to_thread(_load))
        except Exception as exc:
            logger.warning("local-harness-presets probe failed: %s", exc, exc_info=True)
            # Fail closed: empty picker when the probe errors.
            return JSONResponse({"presets": [], "workers": [], "catalog": [], "available_ids": []})

    @router.post("/workflows/studio/routing-assessment")
    async def workflow_studio_routing_assessment(data: StudioRoutingAssessmentRequest):
        """Assess operational complexity and failure signals before an automatic route is chosen."""
        from distr.core.kanban.ticket_policy import resolve_ticket_cli_route
        from distr.core.workflow.execution_mode import assess_development_request, choose_development_execution_mode

        instruction = str(data.instruction or "").strip()
        assessment = await asyncio.to_thread(
            assess_development_request, instruction, ticket_title=data.ticket_title,
            ticket_description=data.ticket_description, has_images=data.has_images,
        )
        complexity, signals = assessment["complexity"], assessment["signals"]

        def resolve_route():
            if data.chat_id is None and data.ticket_id is None and not data.board_key:
                return resolve_ticket_cli_route(None, complexity)
            from distr.core.db import Chat, get_session
            from distr.core.workflow.development_threads import development_board, development_thread_metadata

            with get_session() as db:
                ticket_id, board_key = data.ticket_id, data.board_key
                if data.chat_id is not None:
                    root = db.get(Chat, data.chat_id)
                    metadata = development_thread_metadata(root) if root and root.parent_id is None else {}
                    if not metadata:
                        raise LookupError("Development thread not found.")
                    ticket_id, board_key = metadata.get("ticket_id"), metadata.get("board_key")
                board = development_board(db, ticket_id=ticket_id, board_key=board_key)
                if board is None and data.chat_id is None:
                    raise LookupError("The selected board or ticket is unavailable.")
                return resolve_ticket_cli_route(None, complexity, board=board)

        try:
            resolved = await asyncio.to_thread(resolve_route)
        except (LookupError, ValueError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        route = dict(resolved)
        route.pop("complexity", None)
        if data.has_images and route.get("model") not in {"", "auto"}:
            route["model"] = "auto"
        execution_mode = choose_development_execution_mode(instruction, assessment=assessment)
        harness = str(route.get("backend") or "pi")
        return JSONResponse({
            **assessment,
            "route": route,
            "execution_mode": execution_mode["mode"],
            "execution_mode_reason": execution_mode["reason"],
            "execution_mode_signals": execution_mode["signals"],
            "reason": (
                f"{complexity.title()} → {harness}"
                + (f" · {execution_mode['mode']}" if execution_mode.get("mode") else "")
                + (f" with {', '.join(signals)} signals" if signals else "")
            ),
        })


    @router.post("/workflows/studio/tasks")
    async def workflow_studio_create_task(data: StudioTaskCreateRequest):
        """Create one durable Development thread and dispatch its IDE agent."""
        prompt = str(data.prompt or "").strip()
        if not prompt:
            return JSONResponse({"detail": "A task prompt is required."}, status_code=422)
        route_mode = str(data.route_mode or "auto").strip().lower()
        from distr.core.workflow.development_harness import DevelopmentScopeChanged
        from distr.core.workflow.development_threads import (
            DevelopmentThreadConflict, normalize_autonomy_level, require_unlinked_local_ticket_scope,
        )

        local_ticket = data.ticket_id is not None and str(data.board_key or "").startswith("decisions:")
        autonomy_level = normalize_autonomy_level(data.autonomy_level) or str(data.autonomy_level or "").strip().lower()
        execution_profile = str(data.execution_profile or "code").strip().lower()
        if route_mode not in {"auto", "manual"}:
            return JSONResponse({"detail": "route_mode must be auto or manual"}, status_code=422)
        if autonomy_level not in {"full", "approval", "plan", "goal"}:
            return JSONResponse(
                {"detail": f"Unknown autonomy level {autonomy_level!r}; expected full, goal, plan or approval."},
                status_code=422,
            )
        if execution_profile not in {"code", "design", "research", "operations"}:
            return JSONResponse({"detail": "Unknown execution profile"}, status_code=422)
        chat_id = None
        linked_local_ticket = False
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
                if local_ticket:
                    try:
                        _, project_id = require_unlinked_local_ticket_scope(db, data.ticket_id, data.board_key)
                    except LookupError as exc:
                        raise DevelopmentThreadConflict(str(exc)) from exc
                    if data.project_id != project_id:
                        raise DevelopmentThreadConflict("The ticket's project changed. Refresh the board and reopen the ticket.")
                elif data.ticket_id is not None and db.get(KanbanTicket, int(data.ticket_id)) is None:
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

            if data.incoming_source_context:
                from distr.core.incoming.source_context import register_source_context

                await asyncio.to_thread(register_source_context, int(chat_id), {
                    **data.incoming_source_context, 'project_id': data.project_id,
                })
            if data.attachments:
                from distr.core.workflow.development_harness import _safe_turn_attachments
                from distr.core.incoming.media import register_thread_attachments

                await asyncio.to_thread(register_thread_attachments, int(chat_id), _safe_turn_attachments(data.attachments))
                from distr.core.incoming.transcription import prepare_thread_transcripts
                await asyncio.to_thread(prepare_thread_transcripts, int(chat_id))

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
                require_current_local_ticket=local_ticket,
            )
            linked_local_ticket = local_ticket
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
            from distr.core.workflow.development_threads import update_development_model_route

            await asyncio.to_thread(
                update_development_model_route,
                int(chat_id),
                route_mode=route_mode,
                backend=str(data.backend or "").strip() if route_mode == "manual" else None,
                provider=provider if route_mode == "manual" else None,
                model_name=model_name if route_mode == "manual" else None,
                reasoning_effort=str(data.reasoning_effort or "medium").strip().lower(),
                service_tier=str(data.service_tier or "standard").strip().lower(),
            )
            if ticket_id is not None and not local_ticket:
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

                    run_metadata = {
                        "skill_ids": skill_ids,
                        "routing_assessment": data.routing_assessment or {},
                    }
                    if data.skip_human_checkpoints:
                        run_metadata["skip_human_checkpoints"] = True
                    if data.use_playwright:
                        run_metadata["use_playwright"] = True
                    if route_mode == "manual":
                        if str(data.backend or "").strip().lower() == "pi":
                            from distr.core.project_cli_backends.timing import resolve_pi_model_provider

                            # The chat/settings provider (e.g. OpenAI for Luna)
                            # is not a Pi provider for ``vendor/model`` ids.
                            provider = resolve_pi_model_provider(model_name, provider) or provider
                        run_metadata["model_route"] = {
                            "route_mode": "manual",
                            "backend": str(data.backend or "").strip(),
                            "provider": provider,
                            "model_name": model_name,
                            "reasoning_effort": str(data.reasoning_effort or "medium").strip().lower(),
                            "service_tier": str(data.service_tier or "standard").strip().lower(),
                        }
                        run_metadata["execution_route"] = {
                            "route_mode": "manual",
                            "backend": str(data.backend or "").strip(),
                            "model_provider": provider,
                            "model": model_name or "auto",
                            "source": "thread_model_route",
                            "complexity": str((data.routing_assessment or {}).get("complexity") or "medium"),
                        }
                    execution = await asyncio.to_thread(
                        dispatch_work_item,
                        workflow_id=int(data.workflow_id),
                        chat_id=int(chat_id),
                        context=prompt,
                        project_id=data.project_id,
                        ticket_id=ticket_id,
                        source_type="studio_thread",
                        run_metadata=run_metadata,
                        dispatch_async=True,
                    )
                else:
                    from distr.core.workflow.development_harness import dispatch_development_prompt

                    execution = await asyncio.to_thread(
                        dispatch_development_prompt,
                        int(chat_id),
                        prompt,
                        routing_assessment={**data.routing_assessment, "route_source": "studio_preview"} if data.routing_assessment else {},
                        attachments=data.attachments or [],
                        skill_ids=skill_ids,
                        use_playwright=bool(data.use_playwright),
                        dispatch_async=True,
                    )
                if isinstance(execution, dict) and execution.get("error"):
                    # The run was rejected (e.g. another ticket is already running for
                    # this project). The chat was created above, so remove it instead of
                    # leaving an orphaned thread with no run behind.
                    try:
                        from distr.core.workflow.development_control import delete_thread

                        await asyncio.to_thread(delete_thread, int(chat_id))
                    except Exception:
                        logger.warning("Could not remove rejected studio thread %s", chat_id, exc_info=True)
                    return JSONResponse(
                        {
                            "chat_id": chat_id,
                            "detail": execution.get("error"),
                            "next_action": "Open the thread and choose another provider or project folder before retrying.",
                        },
                        status_code=409,
                    )
                from distr.core.workflow.development_control import resume_thread_time

                track_time = True
                if data.project_id is not None:
                    with get_session() as db:
                        selected_project = db.get(Project, int(data.project_id))
                        track_time = bool(selected_project is None or selected_project.start_time_tracker)
                if track_time:
                    await asyncio.to_thread(resume_thread_time, int(chat_id))

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
            if isinstance(e, DevelopmentScopeChanged) and linked_local_ticket:
                return JSONResponse({"detail": str(e), "chat_id": chat_id, "task": None}, status_code=409)
            logger.error("Studio task creation failed: %s", e, exc_info=True)
            if chat_id is not None:
                # Never leave a half-created thread behind: the UI would
                # otherwise navigate to (or list) a thread with no run.
                try:
                    from distr.core.db import Chat, get_session

                    with get_session() as db:
                        orphan = db.get(Chat, int(chat_id))
                        if orphan is not None:
                            orphan.is_archived = True
                            db.commit()
                except Exception:
                    logger.warning("Could not archive orphan Development thread %s", chat_id, exc_info=True)
            return JSONResponse(
                {"detail": f"The thread could not be started: {e}", "task": None},
                status_code=(
                    409 if isinstance(e, (DevelopmentThreadConflict, DevelopmentScopeChanged))
                    else 422 if isinstance(e, ValueError) else 500
                ),
            )


    @router.post("/workflows/studio/tasks/{chat_id}/messages")
    async def workflow_studio_message(chat_id: int, data: StudioThreadMessageRequest, wait: bool = False, timeout: Optional[float] = None):
        """Route a Development message directly to its persistent IDE harness."""
        from distr.core.incoming.proposals import (
            MergeHandoffPending, ReviewedMergeAttachmentUnavailable, restore_reviewed_thread_merge_attachments,
        )
        from distr.core.workflow.development_harness import DevelopmentExecutionBusy, require_direct_execution_available

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
                if metadata.get("source_type") == "plan_workspace":
                    return JSONResponse({"detail": "Continue this conversation in Plan so edits remain scoped to planning artifacts."}, status_code=409)
                if not metadata:
                    return JSONResponse({"detail": "This conversation is not a development thread."}, status_code=409)
                autonomy_level = str(chat.autonomy_level or "full").strip().lower()
                project_id = int(chat.project_id) if chat.project_id is not None else None
            skill_ids = filter_known_skill_ids(list(data.skill_ids or []))[:12]
            from distr.core.chat import is_duplicate_pending_input

            if await asyncio.to_thread(is_duplicate_pending_input, int(chat_id), message):
                logger.info("Studio message ignored: identical pending input for thread %s", chat_id)
                return JSONResponse({"success": True, "duplicate": True, "started": False, "message": "Already sent; waiting for the reply."})
            workflow_id = int(metadata.get("workflow_id") or 0) or None
            direct = autonomy_level != "approval" and workflow_id is None
            save_with_claim = direct and str(metadata.get("source_type") or "").lower() != "automation"
            if direct:
                existing_execution = metadata.get("execution")
                if isinstance(existing_execution, dict):
                    require_direct_execution_available(existing_execution)
            elif workflow_id is not None and autonomy_level != "approval" and project_id is not None:
                await asyncio.to_thread(restore_reviewed_thread_merge_attachments, int(chat_id), project_id)
            turn_id = None
            if not save_with_claim:
                turn_id = await asyncio.to_thread(
                    ChatService.add_user_message,
                    int(chat_id),
                    message,
                    metadata={"skill_ids": skill_ids} if skill_ids else None,
                )
            if autonomy_level == "approval":
                return JSONResponse(
                    {
                        "success": True,
                        "execution": None,
                        "started": False,
                        "turn_id": turn_id,
                        "message": "Instruction saved and waiting for approval.",
                    }
                )

            if workflow_id is not None:
                from distr.core.workflow.relaunch_guard import relaunch_decision
                from distr.core.workflow.work_dispatch import dispatch_work_item

                relaunch = await asyncio.to_thread(relaunch_decision, int(chat_id), workflow_id, message)
                if not relaunch.get("allow", True):
                    await asyncio.to_thread(
                        ChatService.append_assistant_notice,
                        int(chat_id),
                        str(relaunch.get("notice") or ""),
                    )
                    return JSONResponse(
                        {
                            "success": True,
                            "execution": None,
                            "started": False,
                            "turn_id": turn_id,
                            "requires_resume": True,
                            "previous_run_id": relaunch.get("previous_run_id"),
                            "message": str(relaunch.get("notice") or ""),
                        }
                    )
                run_metadata = dict(relaunch.get("inherit") or {})
                if skill_ids:
                    run_metadata["skill_ids"] = skill_ids
                if data.routing_assessment:
                    run_metadata["routing_assessment"] = data.routing_assessment
                run_metadata.setdefault("skill_ids", skill_ids)
                run_metadata.setdefault("routing_assessment", {})
                if relaunch.get("previous_run_id"):
                    run_metadata["resumed_from_run_id"] = relaunch["previous_run_id"]
                execution = await asyncio.to_thread(
                    dispatch_work_item,
                    workflow_id=workflow_id,
                    chat_id=int(chat_id),
                    context=message,
                    project_id=project_id,
                    ticket_id=metadata.get("ticket_id"),
                    source_type="studio_thread",
                    run_metadata=run_metadata,
                    dispatch_async=True,
                )
            else:
                from distr.core.workflow.development_harness import dispatch_development_prompt

                execution = await asyncio.to_thread(
                    dispatch_development_prompt,
                    int(chat_id),
                    message,
                    routing_assessment={**data.routing_assessment, "route_source": "studio_preview"} if data.routing_assessment else {},
                    attachments=data.attachments or [],
                    skill_ids=skill_ids,
                    use_playwright=bool(data.use_playwright),
                    dispatch_async=True,
                    save_prompt=save_with_claim,
                )
                if save_with_claim:
                    turn_id = execution.get("turn_id")
            if isinstance(execution, dict) and execution.get("error"):
                await asyncio.to_thread(
                    ChatService.append_assistant_notice,
                    int(chat_id),
                    f"Execution could not start: {execution['error']}",
                )
                return JSONResponse({"detail": execution["error"], **({"code": execution["code"]} if execution.get("code") else {})}, status_code=409)
            from distr.core.workflow.development_control import resume_thread_time

            track_time = True
            if project_id is not None:
                from distr.core.db.projects import Project

                with get_session() as db:
                    selected_project = db.get(Project, int(project_id))
                    track_time = bool(selected_project is None or selected_project.start_time_tracker)
            if track_time:
                await asyncio.to_thread(resume_thread_time, int(chat_id))
            from distr.gui.web.workflow_events import increment_workflow_updated

            increment_workflow_updated()
            response_payload = {"success": True, "execution": execution, "turn": execution, "started": True, "turn_id": turn_id}
            if turn_id is not None:
                from distr.gui.web.routes.api_extensions import turn_response_fields

                response_payload.update(
                    await turn_response_fields(int(chat_id), turn_id=int(turn_id), wait=bool(wait), timeout=timeout)
                )
            return JSONResponse(response_payload)
        except ReviewedMergeAttachmentUnavailable as exc:
            return JSONResponse({"code": "proposal_attachment_unavailable", "detail": str(exc)}, status_code=409)
        except MergeHandoffPending as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except DevelopmentExecutionBusy as exc:
            return JSONResponse({"code": "development_execution_busy", "detail": str(exc)}, status_code=409)
        except Exception as exc:
            logger.error("Studio development message failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.get("/workflows/studio/tasks/{chat_id}/execution")
    async def workflow_studio_execution(chat_id: int):
        try:
            from distr.core.turn_runtime.service import active_turn_stop_pending, native_steering_available
            from distr.core.workflow.development_harness import (
                ACTIVE_EXECUTION_STATUSES, development_execution_state, exact_development_stop_target,
            )

            state = await asyncio.to_thread(development_execution_state, int(chat_id))
            active_turn = state.get("turn_id")
            if (state.get("runtime_id") in {"native", "cli_harness"} and type(active_turn) is int
                    and state.get("status") in ACTIVE_EXECUTION_STATUSES and not state.get("completed_at")):
                state = {**state, "stop_pending": active_turn_stop_pending(chat_id, active_turn)}
            stop = exact_development_stop_target(state)
            turn_id = stop.get("turn_id") if stop else None
            steer = {"turn_id": turn_id} if turn_id and native_steering_available(chat_id, turn_id) else None
            if state.get("direct") is False and state.get("status") == "waiting" and not state.get("completed_at"):
                from distr.core.workflow.interactions import development_pending_interactions, telegram_checkpoint_delivery_status

                pending = await asyncio.to_thread(
                    development_pending_interactions, run_id=state["id"], development_chat_id=chat_id,
                )
                if len(pending) == 1:
                    item = pending[0]
                    if item["status"] == "resolving":
                        state["checkpoint_decision_pending"] = True
                    elif item["status"] == "unconfirmed":
                        state["checkpoint_decision_unconfirmed"] = True
                    else:
                        try:
                            allowed = json.loads(item["allowed_actions"] or "[]")
                        except (TypeError, ValueError):
                            allowed = []
                        allowed = [action for action in allowed if isinstance(action, str)] if isinstance(allowed, list) else []
                        if item["kind"] == "qualification_telegram_voice":
                            allowed = [action for action in allowed if action != "continue"]
                        if allowed:
                            state["checkpoint"] = {
                                "token": item["token"], "allowed_actions": allowed,
                                "telegram_delivery": telegram_checkpoint_delivery_status(item),
                            }
                            if item.get("review_notice"):
                                state["waiting_prompt"] = item["review_notice"]
            return JSONResponse({**state, "stop_pending": state.get("stop_pending") is True,
                                 "exact_stop": stop, "exact_steer": steer})
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception as exc:
            logger.error("Studio execution state failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/execution/checkpoint",
                 dependencies=[Depends(require_internal_token_dependency)])
    async def workflow_studio_checkpoint_decision(chat_id: int, data: StudioCheckpointDecisionRequest):
        from distr.core.workflow.development_harness import development_execution_state
        from distr.core.workflow.interactions import resolve_interaction

        headers = {"Cache-Control": "no-store"}
        try:
            state = await asyncio.to_thread(development_execution_state, chat_id)
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404, headers=headers)
        can_claim = (state.get("direct") is False and state.get("id") == data.run_id
                     and state.get("status") == "waiting" and not state.get("completed_at"))
        response = data.response_text.strip()
        if data.action == "feedback" and not response:
            return JSONResponse({"detail": "Enter revision guidance before submitting."}, status_code=422, headers=headers)
        result = await asyncio.to_thread(
            resolve_interaction, token=data.token, action=data.action, response_text=response,
            source="web", resolver_id="paired-app-view", background=True,
            development_scope=(chat_id, data.run_id), allow_new_claim=can_claim,
        )
        if result.get("error"):
            error = {"detail": result["error"]}
            if result.get("code"):
                error["code"] = result["code"]
            return JSONResponse(error, status_code=result.get("status_code", 409), headers=headers)
        return JSONResponse(result, headers=headers)


    @router.get("/workflows/studio/tasks/{chat_id}/execution/client-reply",
                dependencies=[Depends(require_internal_token_dependency)])
    async def workflow_studio_client_reply_review(chat_id: int, run_id: int = Query(gt=0)):
        from distr.core.workflow.development_harness import development_client_reply_review

        headers = {"Cache-Control": "no-store"}
        try:
            review = await asyncio.to_thread(development_client_reply_review, chat_id, run_id)
            return JSONResponse(review, headers=headers)
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404, headers=headers)


    @router.post("/workflows/studio/tasks/{chat_id}/execution/stop")
    async def workflow_studio_stop_execution(
        chat_id: int, execution_session_id: Optional[int] = Body(default=None, embed=True, gt=0),
    ):
        try:
            from distr.core.workflow.development_harness import stop_development_execution

            options = {"expected_session_id": execution_session_id} if execution_session_id is not None else {}
            result = await asyncio.to_thread(stop_development_execution, int(chat_id), **options)
            if result.get("stop_requested") is False:
                return JSONResponse({"detail": "This worker is no longer available to stop."}, status_code=409)
            return JSONResponse(result)
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception as exc:
            logger.error("Studio execution stop failed: %s", exc, exc_info=True)
            return JSONResponse({"detail": str(exc)}, status_code=500)

    @router.post("/workflows/studio/tasks/{chat_id}/execution/stop-exact")
    async def workflow_studio_stop_exact_execution(
        chat_id: int, execution_session_id: Optional[int] = Body(default=None, gt=0),
        workflow_run_id: Optional[int] = Body(default=None, gt=0),
        turn_id: Optional[int] = Body(default=None, gt=0),
    ):
        from distr.core.workflow.development_harness import stop_development_execution

        options = {name: value for name, value in (
            ("expected_session_id", execution_session_id),
            ("expected_workflow_run_id", workflow_run_id),
            ("expected_turn_id", turn_id),
        ) if value is not None}
        if len(options) != 1:
            return JSONResponse({"detail": "Choose one execution to stop."}, status_code=422)
        try:
            result = await asyncio.to_thread(stop_development_execution, int(chat_id), **options)
            if not result.get("stop_requested"):
                return JSONResponse({"detail": "This run changed or is no longer available to stop."}, status_code=409)
            return JSONResponse({"stop_requested": True, "stopped": bool(result.get("stopped"))})
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception:
            logger.exception("Studio exact stop failed for thread %s", chat_id)
            return JSONResponse({"detail": "The stop request could not be confirmed."}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/execution/steer-exact")
    async def workflow_studio_steer_exact_execution(chat_id: int, data: StudioSteeringRequest):
        from distr.core.workflow.development_control import steer_exact_turn

        try:
            command = await asyncio.to_thread(
                steer_exact_turn, chat_id, data.turn_id, data.expected_project_id, data.submission_id, data.content,
            )
            return JSONResponse({
                "chat_id": command["chat_id"], "turn_id": data.turn_id, "submission_id": command["source_ref"],
                "status": command["status"], "summary": command["result_summary"],
            })
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except Exception:
            logger.exception("Studio exact steering could not be confirmed for thread %s", chat_id)
            return JSONResponse({"detail": "The steering request could not be confirmed."}, status_code=503)


    @router.get("/workflows/studio/tasks/{chat_id}/execution/steer-exact")
    async def workflow_studio_steering_receipt(
        chat_id: int, turn_id: int = Query(gt=0), expected_project_id: int = Query(gt=0),
        submission_id: str = Query(pattern=r"^[a-f0-9]{32}$"),
    ):
        from distr.core.workflow.development_control import exact_steering_receipt

        try:
            return JSONResponse(await asyncio.to_thread(
                exact_steering_receipt, chat_id, turn_id, expected_project_id, submission_id,
            ), headers={"Cache-Control": "no-store"})
        except LookupError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=404)
        except Exception:
            logger.exception("Studio steering receipt lookup failed for thread %s", chat_id)
            return JSONResponse({"detail": "The steering receipt could not be checked."}, status_code=503)


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

            scope_fields = {"project_id", "board_key", "board_provider", "ticket_id", "board_ticket_key"} & data.model_fields_set
            if scope_fields:
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
                    _scope_fields=scope_fields,
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
