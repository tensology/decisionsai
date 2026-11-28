"""Agent tool: turn a mail (or "that mail") into a curated proposal, directly."""

from __future__ import annotations

from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class ClientMailProposalInput(BaseModel):
    message_id: str = Field(default="", description="Mail id, if known (Gmail or Mailshot). Leave empty for 'that mail' just discussed.")
    query: str = Field(default="", description="Sender and/or subject words to find the mail, e.g. 'dana acme checkout'.")
    request: str = Field(default="", description="What the user asked, in their words.")


class ClientMailProposalTool(BaseTool):
    name: str = "client_mail_proposal"
    description: str = (
        "Use when the user asks you to check whether an email or client ticket is already a proposal, or to make it one "
        "and push it as a thread. Finds the mail (read only), checks for an existing proposal, investigates, files ONE curated "
        "proposal in Incoming and sends its Accept/Decline card to Telegram, then waits for approval. "
        "Report its result and stop: do not search boards, run code or call other tools for this request. "
        "Never sends anything to a client."
    )
    args_schema: type[BaseModel] = ClientMailProposalInput

    def _run(self, message_id: str = "", query: str = "", request: str = "", **kwargs: Any) -> str:
        from distr.core.initiative import client_mail_request as cmr

        chat_id, parent = None, None
        try:
            from distr.core.chat_manager import _active_chat_turn

            bound = _active_chat_turn.get()
            if bound and bound[1]:
                chat_id, parent = int(bound[0]), f"turn:{int(bound[0])}:{int(bound[1])}"
        except Exception:
            chat_id, parent = None, None
        message_id = (message_id or "").strip()
        if not message_id and not (query or "").strip():
            message_id = cmr.mail_reference_from_chat(chat_id)
        out = cmr.handle_request(request, message_id=message_id, query=(query or "").strip(), parent=parent)
        return out["message"]

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        import asyncio

        return await asyncio.to_thread(self._run, *args, **kwargs)
