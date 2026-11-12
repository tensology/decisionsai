"""Thread tools HTTP boundary. Existing URLs remain compatible."""
from fastapi.responses import JSONResponse
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
from .models import StudioArtifactCreateRequest, StudioArtifactUpdateRequest
from .review_document import development_review_document


def register_routes(router, templates):
    from fastapi import Request

    def reply_history_conflict(exc):
        payload = {'detail': str(exc), 'code': 'reply_history_unavailable'}
        if exc.source_key:
            payload['source_key'] = exc.source_key
        return JSONResponse(payload, status_code=409)

    @router.get('/workflows/studio/source-reply-surface')
    async def source_reply_surface():
        document = await asyncio.to_thread(
            development_review_document, "Development source reply",
            styles=("threads/styles.css", "threads/source-reply-frame.css"),
            scripts=("threads/source-reply.js", "threads/source-reply-frame.js"),
        )
        return JSONResponse({"version": 1, "document": document}, headers={"Cache-Control": "no-store"})

    @router.get('/workflows/studio/tasks/{chat_id}/source-replies')
    async def source_reply_choices(chat_id: int, response_id: int | None = None):
        from distr.core.incoming.source_reply import ReplyHistoryUnavailableError, proposal_for_thread, reply_review, source_choices
        try:
            def choices():
                sources = source_choices(proposal_for_thread(chat_id))
                drafts, blocked = reply_review(chat_id, response_id) if response_id is not None else ([], {})
                for source in sources:
                    if source['key'] in blocked:
                        source['reply_blocked_reason'] = blocked[source['key']]
                return {'sources': sources, 'drafts': drafts}
            return JSONResponse(await asyncio.to_thread(choices))
        except ReplyHistoryUnavailableError as exc:
            return reply_history_conflict(exc)
        except ValueError as exc:
            return JSONResponse({'detail': str(exc)}, status_code=422)

    @router.post('/workflows/studio/tasks/{chat_id}/source-replies')
    async def source_reply_draft(chat_id: int, request: Request):
        from distr.core.incoming.source_reply import ReplyHistoryUnavailableError, create_draft
        try:
            body = await request.json()
            draft = await asyncio.to_thread(create_draft, chat_id, int(body['response_id']), str(body['source_key']),
                previous=str(body.get('previous') or ''), instruction=str(body.get('instruction') or ''),
                replaces=str(body.get('replaces') or ''))
            return JSONResponse(draft)
        except ReplyHistoryUnavailableError as exc:
            return reply_history_conflict(exc)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({'detail': str(exc)}, status_code=422)
        except Exception:
            logger.exception('Source reply draft failed')
            return JSONResponse({'detail': 'Could not draft the reply. Your text has been kept. Please retry.'}, status_code=502)

    @router.post('/workflows/studio/tasks/{chat_id}/source-replies/{draft_id}/{action}')
    async def source_reply_action(chat_id: int, draft_id: str, action: str, request: Request):
        from distr.core.incoming.source_reply import (
            act_on_draft, ReplyHistoryUnavailableError, ReplyPreflightError, ReplyRewriteRequiredError, ReplySourceChangedError,
        )
        try:
            body = await request.json()
            if action == 'approve' and body.get('approved') is not True:
                return JSONResponse({'detail': 'Approve the displayed reply before sending.'}, status_code=409)
            return JSONResponse(await asyncio.to_thread(act_on_draft, chat_id, draft_id, action, str(body.get('text') or '')))
        except ReplyHistoryUnavailableError as exc:
            return reply_history_conflict(exc)
        except ReplyPreflightError as exc:
            payload = {'detail': str(exc), 'code': 'reply_not_sent',
                       'send_attempted': False, 'draft_status': 'draft'}
            if isinstance(exc, ReplySourceChangedError):
                payload['reason'] = 'source_changed'
            elif isinstance(exc, ReplyRewriteRequiredError):
                payload['reason'] = 'rewrite_required'
            return JSONResponse(payload, status_code=409)
        except ValueError as exc:
            return JSONResponse({'detail': str(exc)}, status_code=409)
        except Exception:
            return JSONResponse({'detail': 'Send was not confirmed. Check the source before retrying; your text is retained.'}, status_code=502)

    @router.get("/workflows/studio/tasks/{chat_id}/artifacts")
    async def workflow_studio_artifacts(chat_id: int):
        try:
            from distr.core.workflow.studio_artifacts import list_studio_artifacts

            return JSONResponse({"items": await asyncio.to_thread(list_studio_artifacts, chat_id)})
        except Exception as e:
            logger.error("Studio artifact list failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.get("/workflows/studio/projects/{project_id}/doctor")
    async def workflow_studio_project_doctor(project_id: int):
        try:
            from pathlib import Path

            from distr.core.db import get_session
            from distr.core.db.projects import Project
            from distr.core.harness_doctor import assess_harness_stack

            with get_session() as db:
                project = db.get(Project, int(project_id))
                if project is None:
                    return JSONResponse({"detail": "Project not found."}, status_code=404)
                folder = str(project.folder_location or "").strip()
            report = await asyncio.to_thread(assess_harness_stack, project_root=Path(folder) if folder else None)
            return JSONResponse(report)
        except Exception as e:
            logger.error("Studio project doctor failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.post("/workflows/studio/tasks/{chat_id}/artifacts")
    async def workflow_studio_add_artifact(chat_id: int, data: StudioArtifactCreateRequest):
        try:
            from distr.core.db import Chat, get_session
            from distr.core.workflow.studio_artifacts import create_studio_artifact

            with get_session() as db:
                if db.get(Chat, int(chat_id)) is None:
                    return JSONResponse({"detail": "Task not found."}, status_code=404)
            row = await asyncio.to_thread(
                create_studio_artifact,
                chat_id=chat_id,
                **data.model_dump(),
            )
            return JSONResponse(row)
        except Exception as e:
            logger.error("Studio artifact creation failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


    @router.patch("/workflows/studio/artifacts/{artifact_id}")
    async def workflow_studio_update_artifact(artifact_id: int, data: StudioArtifactUpdateRequest):
        try:
            from distr.core.workflow.studio_artifacts import update_studio_artifact

            row = await asyncio.to_thread(
                update_studio_artifact,
                artifact_id,
                **data.model_dump(exclude_unset=True),
            )
            if row is None:
                return JSONResponse({"detail": "Artifact not found."}, status_code=404)
            return JSONResponse(row)
        except Exception as e:
            logger.error("Studio artifact update failed: %s", e, exc_info=True)
            return JSONResponse({"detail": str(e)}, status_code=500)


