"""
Actions routes — /actions/*, /last-chat-id
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import json

from ._shared import logger, ActionUpdate, ActionCreate, route_handler


class LastChatIdUpdate(BaseModel):
    last_chat_id: Optional[int] = None


def register_routes(router, templates):

    @router.get("/actions")
    @route_handler("load actions", fallback={"detail": "Failed to load actions"})
    async def get_actions_list():
        """Get list of actions, ordered by last run (then modified)."""
        from distr.core.db import get_session, Action
        from sqlalchemy import desc, nulls_last
        with get_session() as session:
            actions = session.query(Action).order_by(
                nulls_last(desc(Action.last_run_date)),
                desc(Action.modified_date)
            ).all()
            return JSONResponse([{
                "id": a.id, "title": a.title or "",
                "description": a.description or "",
                "additional_trigger_words": a.additional_trigger_words or "[]",
                "is_instruction": bool(a.is_instruction) if a.is_instruction is not None else False,
                "instruction_text": a.instruction_text or "",
                "recording_filename": a.recording_filename or "",
                "last_run_date": a.last_run_date.isoformat() if a.last_run_date else None,
            } for a in actions])

    @router.post("/actions/start-recording")
    @route_handler("start recording")
    async def start_action_recording_api(request: Request):
        """Request to start action recording (emits signal for desktop app)."""
        from distr.core.signals import signal_manager
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        action_id = body.get("action_id")
        if action_id is not None:
            signal_manager.start_action_recording_with_id.emit(int(action_id))
        else:
            signal_manager.start_action_recording.emit()
        return JSONResponse({"success": True})

    @router.post("/actions/stop-recording")
    @route_handler("stop recording")
    async def stop_action_recording_api():
        """Request to stop action recording."""
        from distr.core.signals import signal_manager
        signal_manager.stop_action_recording.emit()
        return JSONResponse({"success": True})

    @router.get("/actions/{action_id}")
    @route_handler("load action")
    async def get_action_by_id(action_id: int):
        """Get a single action by id."""
        from distr.core.db import get_session, Action
        with get_session() as session:
            action = session.query(Action).filter(Action.id == action_id).first()
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            return JSONResponse({
                "id": action.id, "title": action.title or "",
                "description": action.description or "",
                "additional_trigger_words": action.additional_trigger_words or "[]",
                "is_instruction": bool(action.is_instruction) if action.is_instruction is not None else False,
                "instruction_text": action.instruction_text or "",
                "recording_filename": action.recording_filename or "",
                "last_run_date": action.last_run_date.isoformat() if action.last_run_date else None,
            })

    @router.post("/actions")
    @route_handler("create action")
    async def create_action(payload: ActionCreate):
        """Create a new action."""
        from distr.core.db import get_session, Action
        from datetime import datetime
        with get_session() as session:
            action = Action(
                title=(payload.title or "New Action").strip(),
                description=(payload.description or "").strip(),
                additional_trigger_words=payload.additional_trigger_words or "[]",
                is_instruction=payload.is_instruction,
                instruction_text=payload.instruction_text if payload.is_instruction else None,
                action="{}", recording_filename=None,
                created_date=datetime.utcnow(), modified_date=datetime.utcnow(),
            )
            session.add(action)
            session.commit()
            session.refresh(action)
            return JSONResponse({"id": action.id, "success": True})

    @router.put("/actions/{action_id}")
    @route_handler("update action")
    async def update_action(action_id: int, payload: ActionUpdate):
        """Update an action by id."""
        from distr.core.db import get_session, Action
        from datetime import datetime
        with get_session() as session:
            action = session.query(Action).filter(Action.id == action_id).first()
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            if payload.title is not None:
                action.title = payload.title
            if payload.description is not None:
                action.description = payload.description
            if payload.additional_trigger_words is not None:
                action.additional_trigger_words = payload.additional_trigger_words
            if payload.is_instruction is not None:
                action.is_instruction = payload.is_instruction
                if not payload.is_instruction:
                    action.instruction_text = None
            if payload.instruction_text is not None and action.is_instruction:
                action.instruction_text = payload.instruction_text
            action.modified_date = datetime.utcnow()
            session.commit()
            return JSONResponse({"success": True})

    @router.post("/actions/{action_id}/play")
    @route_handler("play action")
    async def play_action_api(action_id: int):
        """Play an action by id. Updates last_run_date."""
        from distr.core.db import get_session, Action
        from distr.core.signals import signal_manager
        from datetime import datetime
        with get_session() as session:
            action = session.query(Action).filter(Action.id == action_id).first()
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            action.last_run_date = datetime.utcnow()
            session.commit()
            name = (action.title or "").strip()
            if not name:
                words = []
                try:
                    words = json.loads(action.additional_trigger_words or "[]")
                except Exception:
                    pass
                name = (words[0] if words else "") or ("action_" + str(action_id))
            signal_manager.play_action_by_name.emit(name)
        return JSONResponse({"success": True})

    @router.delete("/actions/{action_id}")
    @route_handler("delete action")
    async def delete_action(action_id: int):
        """Delete an action by id."""
        from distr.core.db import get_session, Action
        with get_session() as session:
            action = session.query(Action).filter(Action.id == action_id).first()
            if not action:
                raise HTTPException(status_code=404, detail="Action not found")
            session.delete(action)
            session.commit()
            return JSONResponse({"success": True})

    @router.put("/last-chat-id")
    @route_handler("save last_chat_id")
    async def set_last_chat_id(body: LastChatIdUpdate):
        """Persist last-loaded chat ID so refresh restores it."""
        from distr.core.services.settings_service import update_setting
        update_setting("last_chat_id", body.last_chat_id)
        return JSONResponse({"success": True, "last_chat_id": body.last_chat_id})
