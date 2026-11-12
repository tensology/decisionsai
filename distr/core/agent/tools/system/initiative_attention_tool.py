"""Agent tool: what Initiative should set aside, focus on, or route (durable, not free-text memory)."""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class InitiativeAttentionInput(BaseModel):
    action: str = Field(
        default="list",
        description=(
            "ignore_client | unignore_client | ignore_project | unignore_project | focus | unfocus | "
            "link_email | unlink_email | list | waiting"
        ),
    )
    client: str = Field(default="", description="Client name, email address, phone number or WhatsApp/Telegram chat id")
    project: str = Field(default="", description="Project name or id")
    email: str = Field(default="", description="Email address (name@example.com) or domain (@example.com) for link_email")
    note: str = Field(default="", description="Why, in the user's words")
    whole_domain: bool = Field(default=False, description="ignore_client: ignore everyone at that email domain")


class InitiativeAttentionTool(BaseTool):
    name: str = "initiative_attention"
    description: str = (
        "Use when the user says to ignore, stop worrying about, or set aside a client or project, to focus on one, "
        "or to link an email address/account to a project. Saves it durably so it survives new sessions and the "
        "agent honours it (no proactive work or automatic replies), while still flagging any client who is waiting. "
        "Also lists what's set aside and who is still waiting."
    )
    args_schema: type[BaseModel] = InitiativeAttentionInput

    def _run(self, action: str = "list", client: str = "", project: str = "", email: str = "", note: str = "",
             whole_domain: bool = False, **kwargs: Any) -> str:
        from distr.core.initiative import attention

        act = (action or "list").strip().lower().replace("-", "_").replace(" ", "_")
        try:
            if act == "ignore_client":
                entry = attention.ignore_client(client, label=client, note=note, whole_domain=whole_domain)
                waiting = [w for w in attention.waiting_tensions() if w["reason"] == "client_ignored"
                           and w["client"] == entry["label"]]
                spoken = f"Got it. I won't do anything proactive for {entry['label']} or reply to them on my own."
                if waiting:
                    spoken += " " + attention.tension_text(waiting[0])
                result: Any = entry
            elif act == "unignore_client":
                ok = attention.unignore_client(client)
                spoken = f"Okay, {client} is back on my radar." if ok else f"{client} wasn't set aside."
                result = {"removed": ok}
            elif act in {"ignore_project", "unignore_project"}:
                result = attention.set_project_ignored(project, act == "ignore_project", note=note)
                spoken = (f"Okay, I've set {result['name']} aside." if act == "ignore_project"
                          else f"{result['name']} is back on my radar.")
                if act == "ignore_project":
                    waiting = [w for w in attention.waiting_tensions() if w.get("project_id") == result["project_id"]]
                    if waiting:
                        spoken += " " + attention.tension_text(waiting[0])
            elif act == "focus":
                result = attention.set_focus(project or client, note=note)
                spoken = f"I'll keep {project or client} up front."
            elif act == "unfocus":
                result = {"removed": attention.clear_focus(project or client)}
                spoken = "Done."
            elif act == "link_email":
                result = attention.link_address(email or client, project, note=note)
                spoken = f"New mail for {result['address']} will go to {result['project']}."
                if result.get("conflict"):
                    spoken += " That address is now linked to more than one project, so I'll ask which one each time."
            elif act == "unlink_email":
                result = {"removed": attention.unlink_address(email or client, project or None)}
                spoken = "Unlinked." if result["removed"] else "That address wasn't linked."
            elif act == "waiting":
                result = attention.waiting_tensions()
                spoken = ("Nobody is waiting behind something you set aside." if not result
                          else " ".join(attention.tension_text(w) for w in result[:3]))
            else:
                result = attention.snapshot()
                spoken = (f"{len(result['ignored_clients'])} client(s) and {len(result['ignored_project_ids'])} "
                          f"project(s) set aside; {len(result['address_links'])} email link(s).")
        except ValueError as exc:
            return str(exc)
        from distr.core.agent.tool_voice_format import voice_then_reference

        return voice_then_reference(spoken, json.dumps(result, ensure_ascii=False, indent=2, default=str))

    async def _arun(self, **kwargs: Any) -> str:
        return self._run(**kwargs)
