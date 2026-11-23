"""Read-only access to another conversation: a chat agent's chat or a chat by id/title."""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class ReadConversationInput(BaseModel):
    agent_name: str = Field(default="", description="Agent name, e.g. 'Uncle Roger'.")
    chat_id: Optional[int] = Field(default=None, description="Agent conversation or Development thread ID.")
    title: str = Field(default="", description="Words from the chat title when the id is unknown.")
    limit: int = Field(default=8, ge=1, le=30, description="How many recent exchanges to return.")


class ReadConversationTool(BaseTool):
    name: str = "read_conversation"
    description: str = (
        "Read the recent messages of another conversation: a chat agent's chat (by agent name), or a chat or "
        "thread by id or title. Read-only. Use for 'what did I say to Uncle Roger', 'read that conversation', "
        "'read it' when it refers to a thread or agent chat."
    )
    args_schema: type[BaseModel] = ReadConversationInput

    def _run(self, agent_name: str = "", chat_id: Optional[int] = None, title: str = "", limit: int = 8, **_: Any) -> str:
        from distr.core.db import Chat, ChatAgent, get_session

        with get_session() as session:
            root = None
            label = ""
            if agent_name:
                agent = (
                    session.query(ChatAgent)
                    .filter(ChatAgent.name.ilike(f"%{agent_name.strip()}%"))
                    .order_by(ChatAgent.id.asc())
                    .first()
                )
                if agent is None or not agent.main_chat_id:
                    return json.dumps({"ok": False, "error": f"No chat agent named {agent_name!r} with a chat."})
                root = session.get(Chat, int(agent.main_chat_id))
                label = agent.name
            elif chat_id:
                root = session.get(Chat, int(chat_id))
                while root is not None and root.parent_id:
                    root = session.get(Chat, int(root.parent_id))
            elif title:
                root = (
                    session.query(Chat)
                    .filter(Chat.parent_id.is_(None), Chat.title.ilike(f"%{title.strip()}%"))
                    .order_by(Chat.modified_date.desc())
                    .first()
                )
            if root is None:
                return json.dumps({"ok": False, "error": "Conversation not found. Give an agent name, chat id or title."})
            rows = (
                session.query(Chat)
                .filter(Chat.parent_id == root.id)
                .filter((Chat.is_hidden.is_(None)) | (Chat.is_hidden.is_(False)))
                .order_by(Chat.id.desc())
                .limit(int(limit))
                .all()
            )
            exchanges = [
                {
                    "id": int(row.id),
                    "at": row.created_date.isoformat() if row.created_date else "",
                    "user": str(row.input or "")[:800],
                    "assistant": str(row.response or "")[:800],
                }
                for row in reversed(rows)
            ]
            return json.dumps(
                {
                    "ok": True,
                    "chat_id": int(root.id),
                    "title": root.title or "",
                    "agent": label,
                    "exchanges": exchanges,
                },
                ensure_ascii=False,
            )

    async def _arun(self, **kwargs: Any) -> str:
        import asyncio

        return await asyncio.to_thread(self._run, **kwargs)
