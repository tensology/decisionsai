"""Multi-agent API: roster order, primary, orchestrator, profile edit, delete,
private memory, group threads (streamed NDJSON) and agent-to-agent messages."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import List, Optional

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ReorderRequest(BaseModel):
    agent_ids: List[int]


class AgentUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    role: Optional[str] = Field(default=None, max_length=4000)


class GroupCreateRequest(BaseModel):
    agent_ids: List[int]
    title: str = Field(default="", max_length=120)


class GroupMembersRequest(BaseModel):
    agent_ids: List[int]


class GroupMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


class AgentMessageRequest(BaseModel):
    from_agent_id: int
    message: str = Field(min_length=1, max_length=20000)
    group_id: Optional[int] = None


def _bad(exc: Exception, status: int = 422):
    raise HTTPException(status, str(exc))


def register_agent_group_routes(router) -> None:
    from distr.core import agent_groups as ag

    @router.get("/agents")
    async def list_agents():
        return JSONResponse({"agents": await asyncio.to_thread(ag.list_agents)})

    @router.put("/agents/order")
    async def reorder_agents(body: ReorderRequest):
        try:
            return JSONResponse({"agents": await asyncio.to_thread(ag.reorder_agents, body.agent_ids)})
        except ag.GroupError as exc:
            _bad(exc)

    @router.post("/agents/{agent_id}/primary")
    async def make_primary(agent_id: int):
        """Persist the primary. The page then loads its chat into the live runtime (load-in-agent)."""
        try:
            return JSONResponse({"primary_agent": await asyncio.to_thread(ag.set_primary, agent_id)})
        except ag.GroupError as exc:
            _bad(exc, 404 if "not found" in str(exc) else 409)

    @router.patch("/agents/{agent_id}")
    async def update_agent(agent_id: int, body: AgentUpdateRequest):
        try:
            return JSONResponse({"agent": await asyncio.to_thread(
                ag.update_agent, agent_id, name=body.name, role=body.role)})
        except ag.GroupError as exc:
            _bad(exc, 404)
        except ValueError as exc:
            _bad(exc)

    @router.delete("/agents/{agent_id}")
    async def delete_agent(agent_id: int):
        try:
            return JSONResponse(await asyncio.to_thread(ag.delete_agent, agent_id))
        except ag.GroupError as exc:
            _bad(exc, 404 if "not found" in str(exc) else 409)

    @router.get("/agents/{agent_id}/memories")
    async def agent_memories(agent_id: int):
        from distr.core.memory.files import agent_memory_sections
        from distr.core.orchestrator_memory import list_agent_memories

        def load():
            notes = [{"id": f"note:{i}", "content": text} for i, text in enumerate(agent_memory_sections(agent_id))]
            learned = [{"id": f"learned:{m['memory_uid']}", "content": m["content"]} for m in list_agent_memories(agent_id)]
            return {"memories": notes + learned}

        return JSONResponse(await asyncio.to_thread(load))

    @router.delete("/agents/{agent_id}/memories")
    async def clear_agent_memories(agent_id: int, memory_id: Optional[str] = None):
        """Clear one memory (memory_id) or all of this agent's own memories. Shared memory is untouched."""
        from distr.core.memory.files import agent_memory_sections, write_agent_memory_sections
        from distr.core.orchestrator_memory import delete_agent_memories

        def clear():
            if memory_id is None:
                write_agent_memory_sections(agent_id, [])
                delete_agent_memories(agent_id)
                return
            kind, _, ref = memory_id.partition(":")
            if kind == "note" and ref.isdigit():
                sections = agent_memory_sections(agent_id)
                if int(ref) >= len(sections):
                    raise ag.GroupError("Memory not found.")
                del sections[int(ref)]
                write_agent_memory_sections(agent_id, sections)
            elif kind == "learned" and ref:
                if not delete_agent_memories(agent_id, ref):
                    raise ag.GroupError("Memory not found.")
            else:
                raise ag.GroupError("Memory not found.")

        try:
            await asyncio.to_thread(clear)
        except ag.GroupError as exc:
            _bad(exc, 404)
        return JSONResponse({"cleared": True})

    @router.post("/agents/{agent_id}/messages")
    async def agent_message(agent_id: int, body: AgentMessageRequest):
        """Agent-to-agent: from_agent_id sends to agent_id; returns the reply."""
        try:
            return JSONResponse(await asyncio.to_thread(
                ag.message_agent, body.from_agent_id, agent_id, body.message, group_id=body.group_id))
        except ag.GroupError as exc:
            _bad(exc)

    @router.get("/groups")
    async def list_groups():
        return JSONResponse({"groups": await asyncio.to_thread(ag.list_groups)})

    @router.post("/groups")
    async def create_group(body: GroupCreateRequest):
        try:
            return JSONResponse({"group": await asyncio.to_thread(ag.create_group, body.agent_ids, body.title)})
        except ag.GroupError as exc:
            _bad(exc)

    @router.get("/groups/{chat_id}")
    async def get_group(chat_id: int):
        try:
            return JSONResponse(await asyncio.to_thread(ag.group_messages, chat_id))
        except ag.GroupError as exc:
            _bad(exc, 404)

    @router.put("/groups/{chat_id}/members")
    async def set_members(chat_id: int, body: GroupMembersRequest):
        try:
            return JSONResponse({"group": await asyncio.to_thread(ag.set_group_members, chat_id, body.agent_ids)})
        except ag.GroupError as exc:
            _bad(exc)

    @router.delete("/groups/{chat_id}")
    async def archive_group(chat_id: int):
        try:
            await asyncio.to_thread(ag.archive_group, chat_id)
        except ag.GroupError as exc:
            _bad(exc, 404)
        return JSONResponse({"archived": True})

    @router.post("/groups/{chat_id}/messages")
    async def post_group_message(chat_id: int, body: GroupMessageRequest):
        """Stream the turn as NDJSON: message, start/token/done per agent, capped, end."""
        def check():
            from distr.core.db import Chat, get_session

            with get_session() as session:
                if not session.get(Chat, chat_id) or not ag.is_group_chat(session, chat_id):
                    raise ag.GroupError("Group not found.")

        try:
            await asyncio.to_thread(check)
        except ag.GroupError as exc:
            _bad(exc, 404)
        events = ag.stream_turn(chat_id, body.message)

        async def body_iter():
            while True:
                event = await asyncio.to_thread(next, events, None)
                if event is None:
                    return
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(body_iter(), media_type="application/x-ndjson")
