from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from distr.core.tensology_client import TensologyApiError, configured_tensology_client


class TensologyWorkspaceInput(BaseModel):
    action: Literal[
        "capabilities", "list_mail", "read_mail", "list_drafts", "save_draft", "send_mail",
        "list_customers", "list_projects", "sync_project", "list_time_entries",
        "create_time_entry", "update_time_entry", "list_invoices", "get_invoice",
        "create_invoice", "update_invoice",
    ] = Field(description="The Tensology operation to perform.")
    params: dict[str, Any] = Field(default_factory=dict, description="Operation parameters.")
    approved: bool = Field(
        default=False,
        description=(
            "For send_mail and invoice changes only. True only after the user explicitly approved the exact action."
        ),
    )
    idempotency_key: str = Field(default="", description="Stable retry key for mutations; generated when omitted.")


class TensologyWorkspaceTool(BaseTool):
    name: str = "tensology_workspace"
    description: str = (
        "Use the central Tensology connection for Mailshot inbox and drafts, customers, projects, calendar time "
        "entries, and invoices. Prefer this over Gmail when the user says Tensology mail or Mailshot, or when the "
        "active project is linked to Tensology. Reading and drafting are safe. Never send mail unless the user "
        "explicitly instructed sending the exact content or approved a recipient, subject, and body preview; "
        "otherwise save a draft and ask."
    )
    args_schema: type[BaseModel] = TensologyWorkspaceInput

    def _run(self, action: str, params: dict | None = None, approved: bool = False, idempotency_key: str = "") -> str:
        params = dict(params or {})
        client = configured_tensology_client(source=str(params.pop("source", "decisionsai")))
        mutation_key = idempotency_key or f"decisionsai-{uuid.uuid4()}"
        try:
            if action == "capabilities":
                result = client.get("capabilities")
            elif action == "list_mail":
                result = client.get("mail/messages", params)
            elif action == "read_mail":
                result = client.get(f"mail/messages/{params['message_id']}")
            elif action == "list_drafts":
                result = client.get("mail/drafts", params)
            elif action == "save_draft":
                result = client.post("mail/drafts", params, idempotency_key=mutation_key)
            elif action == "send_mail":
                if not approved:
                    return "Approval required: show the recipient, subject, and message preview, then ask whether to send it."
                result = client.post("mail/send", params, idempotency_key=mutation_key, approved=True)
            elif action == "list_customers":
                result = client.get("customers", params)
            elif action == "list_projects":
                result = client.get("projects", params)
            elif action == "sync_project":
                result = client.post("projects", params, idempotency_key=mutation_key)
            elif action == "list_time_entries":
                result = client.get("time-entries", params)
            elif action == "create_time_entry":
                result = client.post("time-entries", params, idempotency_key=mutation_key)
            elif action == "update_time_entry":
                entry_id = params.pop("entry_id")
                result = client.patch(f"time-entries/{entry_id}", params, idempotency_key=mutation_key)
            elif action == "list_invoices":
                result = client.get("invoices", params)
            elif action == "get_invoice":
                result = client.get(f"invoices/{params['invoice_id']}")
            elif action == "create_invoice":
                if not approved:
                    return "Approval required: show the invoice preview, then ask whether to create it."
                result = client.post("invoices", params, idempotency_key=mutation_key, approved=True)
            elif action == "update_invoice":
                if not approved:
                    return "Approval required: show the invoice changes, then ask whether to apply them."
                invoice_id = params.pop("invoice_id")
                result = client.patch(
                    f"invoices/{invoice_id}",
                    params,
                    idempotency_key=mutation_key,
                    approved=True,
                )
            else:
                return f"Unsupported Tensology action: {action}"
            return json.dumps(result, ensure_ascii=False, default=str)
        except KeyError as exc:
            return f"Missing required parameter: {exc.args[0]}"
        except TensologyApiError as exc:
            return f"Tensology error ({exc.code}): {exc}"

    async def _arun(self, **kwargs) -> str:
        return self._run(**kwargs)
