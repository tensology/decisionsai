"""Thread controls HTTP boundary. Existing URLs remain compatible."""
from fastapi.responses import JSONResponse
from typing import Optional
import json
import asyncio
import logging

logger = logging.getLogger(__name__)
from .models import StudioCommandRequest, StudioCommandUpdateRequest, StudioInteractionResolveRequest, StudioModelRouteRequest, StudioPlanTransitionRequest, StudioThreadControlsRequest, StudioTimeUpdateRequest


def register_routes(router, templates):
    @router.get("/workflows/studio/tasks/{chat_id}/plans")
    async def workflow_studio_plan_revisions(chat_id: int):
        try:
            from distr.core.workflow.development_plans import list_plan_revisions

            return JSONResponse({"items": await asyncio.to_thread(list_plan_revisions, chat_id)})
        except Exception as e:
            logger.error("Studio plan revision list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/tasks/{chat_id}/time")
    async def workflow_studio_thread_time(chat_id: int):
        try:
            from distr.core.workflow.development_control import thread_time_state

            return JSONResponse(await asyncio.to_thread(thread_time_state, chat_id))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)


    @router.post("/workflows/studio/tasks/{chat_id}/time/play")
    async def workflow_studio_play_time(chat_id: int):
        try:
            from distr.core.workflow.development_control import resume_thread_time

            return JSONResponse(await asyncio.to_thread(resume_thread_time, chat_id))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)


    @router.post("/workflows/studio/tasks/{chat_id}/time/pause")
    async def workflow_studio_pause_time(chat_id: int):
        try:
            from distr.core.workflow.development_control import pause_thread_time

            return JSONResponse(await asyncio.to_thread(pause_thread_time, chat_id))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)


    @router.patch("/workflows/studio/tasks/{chat_id}/time")
    async def workflow_studio_reset_time(chat_id: int, data: StudioTimeUpdateRequest):
        try:
            from distr.core.workflow.development_control import reset_thread_time

            return JSONResponse(await asyncio.to_thread(reset_thread_time, chat_id, seconds=data.seconds))
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)


    @router.patch("/workflows/studio/plans/{revision_id}")
    async def workflow_studio_transition_plan(revision_id: int, data: StudioPlanTransitionRequest):
        try:
            from distr.core.workflow.development_plans import transition_plan_revision

            plan = await asyncio.to_thread(transition_plan_revision, revision_id, data.status)
            if plan is None:
                return JSONResponse({"detail": "Plan revision not found."}, status_code=404)
            return JSONResponse(plan)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except Exception as e:
            logger.error("Studio plan revision update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/tasks/{chat_id}/commands")
    async def workflow_studio_commands(chat_id: int, include_terminal: bool = True):
        """List ordered instructions from every approved Development control surface."""
        try:
            from distr.core.workflow.development_control import list_commands

            return JSONResponse({"items": await asyncio.to_thread(list_commands, chat_id, include_terminal=include_terminal)})
        except Exception as e:
            logger.error("Studio command list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/commands")
    async def workflow_studio_enqueue_command(chat_id: int, data: StudioCommandRequest):
        try:
            from distr.core.skills.catalog import filter_known_skill_ids
            from distr.core.workflow.development_control import dispatch_command, enqueue_command, resume_thread_time

            metadata = dict(data.metadata or {})
            metadata["skill_ids"] = filter_known_skill_ids(list(metadata.get("skill_ids") or []))[:12]
            command = await asyncio.to_thread(
                enqueue_command,
                chat_id,
                data.content,
                source=data.source,
                source_ref=data.source_ref,
                command_type=data.command_type,
                metadata=metadata,
            )
            await asyncio.to_thread(resume_thread_time, chat_id)
            delivery = await asyncio.to_thread(dispatch_command, int(command["id"]))
            return JSONResponse(
                {
                    "status": "delivered" if delivery.get("dispatched") else "queued",
                    "summary": "Instruction delivered to the active worker." if delivery.get("dispatched") else "Instruction queued safely until a worker can accept it.",
                    "next_actions": [] if delivery.get("dispatched") else ["edit", "cancel"],
                    "artifacts": [delivery.get("command") or command],
                    "delivery": delivery,
                },
                status_code=201,
            )
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Studio command enqueue failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.patch("/workflows/studio/commands/{command_id}")
    async def workflow_studio_update_command(command_id: int, data: StudioCommandUpdateRequest):
        try:
            from distr.core.workflow.development_control import update_command

            command = await asyncio.to_thread(update_command, command_id, content=data.content, cancel=data.cancel)
            if command is None:
                return JSONResponse({"detail": "Command not found."}, status_code=404)
            return JSONResponse(command)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=409)
        except Exception as e:
            logger.error("Studio command update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/commands/dispatch")
    async def workflow_studio_dispatch_commands(chat_id: int):
        try:
            from distr.core.workflow.development_control import dispatch_pending

            return JSONResponse(await asyncio.to_thread(dispatch_pending, chat_id))
        except Exception as e:
            logger.error("Studio command dispatch failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.patch("/workflows/studio/tasks/{chat_id}/controls")
    async def workflow_studio_thread_controls(chat_id: int, data: StudioThreadControlsRequest):
        try:
            from distr.core.workflow.development_control import update_thread_controls

            controls = await asyncio.to_thread(update_thread_controls, chat_id, **data.model_dump(exclude_unset=True))
            return JSONResponse({**controls, "status": "updated", "summary": "Thread controls saved.", "next_actions": [], "artifacts": [controls]})
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Studio thread controls failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.patch("/workflows/studio/tasks/{chat_id}/model-route")
    async def workflow_studio_model_route(chat_id: int, data: StudioModelRouteRequest):
        """Persist provider/model controls and apply them to this chat's workflow."""
        try:
            from distr.core.workflow.development_threads import update_development_model_route

            result = await asyncio.to_thread(
                update_development_model_route,
                chat_id,
                **data.model_dump(),
            )
            return JSONResponse(result)
        except LookupError as e:
            return JSONResponse({"detail": str(e)}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=422)
        except Exception as e:
            logger.error("Studio model route update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/control-state")
    async def workflow_studio_control_state(run_id: Optional[int] = None, chat_id: Optional[int] = None):
        """Return safe cross-surface status and pending controls for Development."""
        try:
            from distr.core.settings import load_settings_from_db
            from distr.core.workflow.interactions import pending_interactions

            settings = await asyncio.to_thread(load_settings_from_db)
            accounts = settings.get("connected_accounts") or []
            if isinstance(accounts, str):
                try:
                    accounts = json.loads(accounts)
                except (TypeError, ValueError):
                    accounts = []
            if not isinstance(accounts, list):
                accounts = []
            telegram_configured = any(
                isinstance(account, dict) and account.get("provider") == "telegram"
                for account in accounts
            )
            telegram_connected = False
            if telegram_configured:
                try:
                    from distr.core.kanban.ticket_workflow_engagement import _telegram_manager_from_app

                    manager = _telegram_manager_from_app()
                    telegram_connected = bool(manager and manager.is_connected())
                except Exception:
                    telegram_connected = False
            interactions = await asyncio.to_thread(pending_interactions)
            if run_id is not None:
                interactions = [item for item in interactions if int(item.get("run_id") or 0) == int(run_id)]
            safe_interactions = [
                {
                    "token": item.get("token"),
                    "run_id": item.get("run_id"),
                    "step_id": item.get("step_id"),
                    "kind": item.get("kind"),
                    "allowed_actions": json.loads(item.get("allowed_actions") or "[]"),
                    "expires_at": item.get("expires_at"),
                    "error": item.get("error"),
                    "telegram_linked": bool(item.get("telegram_chat_id")),
                }
                for item in interactions
            ]
            commands = []
            controls = {}
            if chat_id is not None:
                from distr.core.db import Chat, get_session
                from distr.core.workflow.development_control import list_commands
                from distr.core.workflow.development_threads import development_thread_metadata

                commands = await asyncio.to_thread(list_commands, int(chat_id), include_terminal=False)
                with get_session() as db:
                    chat = db.get(Chat, int(chat_id))
                    controls = development_thread_metadata(chat) if chat else {}
            return JSONResponse(
                {
                    "channels": {
                        "telegram": {
                            "configured": telegram_configured,
                            "connected": telegram_connected,
                        }
                    },
                    "interactions": safe_interactions,
                    "commands": commands,
                    "controls": controls,
                }
            )
        except Exception as e:
            logger.error("Studio control-state load failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/interactions/{token}/resolve")
    async def workflow_studio_resolve_interaction(token: str, data: StudioInteractionResolveRequest):
        """Resolve the same durable checkpoint used by Telegram from the web UI."""
        action = str(data.action or "").strip().lower()
        if not action:
            return JSONResponse({"detail": "An interaction action is required."}, status_code=422)
        try:
            from distr.core.workflow.interactions import resolve_interaction

            result = await asyncio.to_thread(
                resolve_interaction,
                token=token,
                action=action,
                response_text=str(data.response_text or "").strip(),
                source="web",
                resolver_id="development-ui",
                background=True,
            )
            if result.get("error"):
                return JSONResponse(
                    {"detail": result["error"], **result},
                    status_code=int(result.get("status_code") or 409),
                )
            return JSONResponse(result)
        except Exception as e:
            logger.error("Studio interaction resolution failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


