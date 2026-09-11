"""Thread tools HTTP boundary. Existing URLs remain compatible."""
from fastapi.responses import JSONResponse
import asyncio
from pathlib import Path
import logging

logger = logging.getLogger(__name__)
from .models import StudioArtifactCreateRequest, StudioArtifactUpdateRequest


def register_routes(router, templates):
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


