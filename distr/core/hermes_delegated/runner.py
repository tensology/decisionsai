"""Execution runner for Hermes delegated workflow plans."""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any

from .models import DelegatedPlan, DelegatedRunReport, Roadblock
from .roadblocks import build_roadblock_report


class DelegatedWorkflowRunner:
    """Execute typed delegated plans through explicit adapters.

    The runner deliberately depends on small adapter methods instead of raw GUI
    control. Browser/desktop automation remains a fallback exposed by the plan,
    but API/file/project adapters are the first execution route.
    """

    def __init__(
        self,
        *,
        email_adapter: Any = None,
        document_adapter: Any = None,
        scope_adapter: Any = None,
        project_dispatcher: Any = None,
        desktop_adapter: Any = None,
        browser_adapter: Any = None,
        intake_dir: str | None = None,
    ) -> None:
        self.email_adapter = email_adapter
        self.document_adapter = document_adapter
        self.scope_adapter = scope_adapter or DefaultScopeAdapter()
        self.project_dispatcher = project_dispatcher
        self.desktop_adapter = desktop_adapter
        self.browser_adapter = browser_adapter
        self.intake_dir = intake_dir or os.path.join(tempfile.gettempdir(), "decisionsai-delegated-intake")

    def run(self, plan: DelegatedPlan, context: dict[str, Any] | None = None) -> DelegatedRunReport:
        context = dict(context or {})
        if plan.kind == "email_document_scope":
            return self._run_email_document_scope(plan, context)
        if plan.kind == "desktop_sequence":
            return self._run_desktop_sequence(plan, context)
        if plan.kind == "browser_workflow":
            return self._run_browser_workflow(plan, context)
        if plan.kind == "project_handoff":
            return self._run_project_handoff(plan, context)
        return DelegatedRunReport(
            status="blocked",
            plan=plan,
            current_step=plan.steps[0].action if plan.steps else "",
            roadblock=Roadblock(
                code="unsupported_delegated_plan",
                title="This delegated plan is not executable yet",
                detail=f"The runner does not yet execute plan kind '{plan.kind}'.",
                options=["Create a workflow ticket for this request.", "Run the individual tools manually."],
            ),
        )

    def _run_email_document_scope(self, plan: DelegatedPlan, context: dict[str, Any]) -> DelegatedRunReport:
        completed: list[str] = []
        evidence: dict[str, Any] = {}
        sender_hint = _extract_sender_hint(plan.original_instruction)
        completed.append("resolve_contact")
        evidence["contact"] = {"sender_hint": sender_hint}

        if not self.email_adapter or not bool(getattr(self.email_adapter, "connected", True)):
            return _blocked(plan, completed, "search_email", build_roadblock_report("gmail_not_connected"), evidence)

        if not hasattr(self.email_adapter, "search_latest_email"):
            return _blocked(
                plan,
                completed,
                "search_email",
                build_roadblock_report(
                    "gmail_not_connected",
                    "The configured email adapter cannot search email directly yet.",
                ),
                evidence,
            )

        email = self.email_adapter.search_latest_email(
            sender_hint=sender_hint,
            query=_email_query(plan.original_instruction, sender_hint),
        )
        if not email:
            return _blocked(
                plan,
                completed,
                "search_email",
                Roadblock(
                    code="email_not_found",
                    title="No matching email was found",
                    detail=f"I could not find a latest email matching sender '{sender_hint or 'unknown'}'.",
                    options=["Try a different sender name.", "Upload the document directly.", "Broaden the email search."],
                ),
                evidence,
            )
        completed.append("search_email")
        evidence["email"] = email

        if not hasattr(self.email_adapter, "download_attachments"):
            return _blocked(
                plan,
                completed,
                "download_attachments",
                Roadblock(
                    code="attachment_download_not_supported",
                    title="Attachment download is not available",
                    detail="The selected email adapter found the email but cannot download attachments.",
                    options=["Use browser automation fallback.", "Upload the attachment directly.", "Read only the email body."],
                ),
                evidence,
            )

        attachments = self.email_adapter.download_attachments(
            message_id=str(email.get("message_id") or email.get("id") or ""),
            destination_dir=self.intake_dir,
        )
        if not attachments:
            return _blocked(
                plan,
                completed,
                "download_attachments",
                Roadblock(
                    code="attachment_not_found",
                    title="No relevant attachment was found",
                    detail="I found the email but did not find a downloadable attachment.",
                    options=["Use the email body only.", "Try a different email.", "Upload the document directly."],
                ),
                evidence,
            )
        completed.append("download_attachments")
        evidence["attachments"] = attachments

        if not self.document_adapter or not hasattr(self.document_adapter, "extract"):
            return _blocked(
                plan,
                completed,
                "extract_document",
                Roadblock(
                    code="document_extractor_unavailable",
                    title="Document extraction is not available",
                    detail="The workflow cannot extract the downloaded attachment without a document adapter.",
                    options=["Upload plain text.", "Install document extraction dependencies.", "Summarize the email body only."],
                ),
                evidence,
            )

        documents: list[dict[str, Any]] = []
        for attachment in attachments:
            path = str(attachment.get("path") or "")
            text = self.document_adapter.extract(path)
            if isinstance(text, str) and text.lower().startswith("error: password"):
                return _blocked(plan, completed, "extract_document", build_roadblock_report("password_protected_document", text), evidence)
            documents.append({"path": path, "name": attachment.get("name") or os.path.basename(path), "text": text})
        completed.append("extract_document")
        evidence["documents"] = documents

        scope = self.scope_adapter.scope(
            instruction=plan.original_instruction,
            email=email,
            documents=documents,
        )
        completed.append("scope_execution")
        evidence["scope"] = scope

        if plan.target_backend:
            if not self.project_dispatcher or not hasattr(self.project_dispatcher, "dispatch"):
                return _blocked(
                    plan,
                    completed,
                    "dispatch_project_handoff",
                    build_roadblock_report("backend_not_ready"),
                    evidence,
                )
            result = self.project_dispatcher.dispatch(
                backend_id=plan.target_backend,
                instruction=_handoff_instruction(plan, scope),
                scope=scope,
                context=context,
            )
            completed.append("dispatch_project_handoff")
            evidence["handoff"] = {
                "success": bool(getattr(result, "success", False)),
                "backend_id": getattr(result, "backend_id", plan.target_backend),
                "output": (getattr(result, "output", "") or "")[:4000],
                "error": (getattr(result, "error", "") or "")[:1000],
            }
            if not evidence["handoff"]["success"]:
                return _blocked(plan, completed, "dispatch_project_handoff", build_roadblock_report("backend_not_ready", evidence["handoff"]["error"]), evidence)

        return DelegatedRunReport(status="completed", plan=plan, completed_steps=completed, evidence=evidence)

    def _run_browser_workflow(self, plan: DelegatedPlan, context: dict[str, Any]) -> DelegatedRunReport:
        completed: list[str] = []
        evidence: dict[str, Any] = {
            "browser_task": {
                "instruction": plan.original_instruction,
                "preferred_route": context.get("preferred_route") or "playwright",
            }
        }
        completed.append("prepare_browser_task")

        adapter = self.browser_adapter
        if not adapter or not hasattr(adapter, "execute"):
            return _blocked(
                plan,
                completed,
                "execute_browser_actions",
                Roadblock(
                    code="browser_adapter_unavailable",
                    title="Browser automation is not available",
                    detail="I need a Playwright or browser-use adapter before I can execute this browser workflow.",
                    options=[
                        "Install and configure Playwright.",
                        "Use the remote browser manually and send me the result.",
                        "Retry with desktop accessibility fallback.",
                    ],
                ),
                evidence,
            )

        result = adapter.execute(instruction=plan.original_instruction, context=context)
        evidence["browser"] = result
        if not bool(result.get("success")):
            return _blocked(
                plan,
                completed,
                "execute_browser_actions",
                Roadblock(
                    code="browser_automation_failed",
                    title="Browser automation failed",
                    detail=str(result.get("error") or result.get("output") or "The browser adapter did not complete the requested task."),
                    options=[
                        "Retry with browser-use fallback.",
                        "Retry with desktop accessibility.",
                        "Ask me for the missing URL, login state, or selector detail.",
                    ],
                ),
                evidence,
            )
        completed.append("execute_browser_actions")
        completed.append("verify_browser_result")
        return DelegatedRunReport(status="completed", plan=plan, completed_steps=completed, evidence=evidence)

    def _run_project_handoff(self, plan: DelegatedPlan, context: dict[str, Any]) -> DelegatedRunReport:
        completed: list[str] = []
        evidence: dict[str, Any] = {}
        completed.append("resolve_project_context")
        evidence["project_context"] = {
            "project_id": context.get("project_id"),
            "target_backend": plan.target_backend,
        }

        packet = {
            "summary": "Execute the requested project handoff.",
            "tasks": [plan.original_instruction],
            "risks": ["Report blockers before making irreversible changes."],
            "original_instruction": plan.original_instruction,
        }
        completed.append("prepare_handoff_packet")
        evidence["handoff_packet"] = packet

        if not self.project_dispatcher or not hasattr(self.project_dispatcher, "dispatch"):
            return _blocked(
                plan,
                completed,
                "dispatch_project_handoff",
                build_roadblock_report("backend_not_ready"),
                evidence,
            )

        backend_id = plan.target_backend or "codex"
        result = self.project_dispatcher.dispatch(
            backend_id=backend_id,
            instruction=_direct_handoff_instruction(plan),
            scope=packet,
            context=context,
        )
        completed.append("dispatch_project_handoff")
        evidence["handoff"] = {
            "success": bool(getattr(result, "success", False)),
            "backend_id": getattr(result, "backend_id", backend_id),
            "output": (getattr(result, "output", "") or "")[:4000],
            "error": (getattr(result, "error", "") or "")[:1000],
        }
        if not evidence["handoff"]["success"]:
            return _blocked(
                plan,
                completed,
                "dispatch_project_handoff",
                build_roadblock_report("backend_not_ready", evidence["handoff"]["error"]),
                evidence,
            )
        return DelegatedRunReport(status="completed", plan=plan, completed_steps=completed, evidence=evidence)

    def _run_desktop_sequence(self, plan: DelegatedPlan, context: dict[str, Any]) -> DelegatedRunReport:
        completed: list[str] = []
        evidence: dict[str, Any] = {}
        adapter = self.desktop_adapter
        if not adapter:
            return _blocked(
                plan,
                completed,
                "capture_source_content",
                Roadblock(
                    code="desktop_adapter_unavailable",
                    title="Desktop automation is not available",
                    detail="I need a desktop adapter or sidecar-backed tools before I can execute this sequence.",
                    options=[
                        "Start the sidecar and retry.",
                        "Use direct file instructions instead.",
                        "Create a workflow ticket for manual review.",
                    ],
                ),
                evidence,
            )

        source_text = adapter.capture_source_content(plan.original_instruction)
        if not source_text:
            return _blocked(
                plan,
                completed,
                "capture_source_content",
                Roadblock(
                    code="source_content_missing",
                    title="Source content was not available",
                    detail="I could not determine what text or file content should be copied.",
                    options=["Put the content on the clipboard.", "Attach the file directly.", "Tell me the exact text to use."],
                ),
                evidence,
            )
        completed.append("capture_source_content")
        evidence["source_length"] = len(str(source_text))

        if not adapter.set_clipboard(str(source_text)):
            return _blocked(plan, completed, "set_clipboard", _desktop_step_roadblock("set_clipboard"), evidence)
        completed.append("set_clipboard")

        focus = adapter.launch_or_focus_app(plan.original_instruction)
        if not focus:
            return _blocked(plan, completed, "launch_or_focus_app", _desktop_step_roadblock("launch_or_focus_app"), evidence)
        completed.append("launch_or_focus_app")
        evidence["focused_app"] = focus

        destination_path = adapter.create_or_open_file(plan.original_instruction)
        if not destination_path:
            return _blocked(plan, completed, "create_or_open_file", _desktop_step_roadblock("create_or_open_file"), evidence)
        completed.append("create_or_open_file")
        evidence["destination_path"] = destination_path

        if not adapter.write_text(destination_path, str(source_text)):
            return _blocked(plan, completed, "write_text", _desktop_step_roadblock("write_text"), evidence)
        completed.append("write_text")

        if not adapter.verify_result(destination_path, str(source_text)):
            return _blocked(plan, completed, "verify_result", _desktop_step_roadblock("verify_result"), evidence)
        completed.append("verify_result")
        evidence["verified"] = True

        return DelegatedRunReport(status="completed", plan=plan, completed_steps=completed, evidence=evidence)


class DefaultScopeAdapter:
    """Small deterministic scope builder used until an LLM/workflow planner is injected."""

    def scope(self, *, instruction: str, email: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
        text = "\n".join(str(doc.get("text") or "") for doc in documents)
        tasks = _extract_task_lines(text)
        return {
            "summary": _first_sentence(text) or "Scope the requested document changes.",
            "tasks": tasks or ["Review the extracted document and convert requested changes into implementation tasks."],
            "risks": ["Confirm acceptance criteria before modifying project files."],
            "source_email": {
                "message_id": email.get("message_id") or email.get("id"),
                "from": email.get("from"),
                "subject": email.get("subject"),
            },
            "original_instruction": instruction,
        }


def _blocked(
    plan: DelegatedPlan,
    completed: list[str],
    current_step: str,
    roadblock: Roadblock,
    evidence: dict[str, Any],
) -> DelegatedRunReport:
    return DelegatedRunReport(
        status="blocked",
        plan=plan,
        completed_steps=list(completed),
        current_step=current_step,
        roadblock=roadblock,
        evidence=evidence,
    )


def _desktop_step_roadblock(step: str) -> Roadblock:
    return Roadblock(
        code=f"{step}_failed",
        title=f"Desktop step failed: {step}",
        detail=f"The desktop adapter could not complete '{step}'.",
        options=["Retry with sidecar running.", "Use direct file operations.", "Ask me for the missing detail."],
    )


def _extract_sender_hint(instruction: str) -> str:
    match = re.search(r"\bfrom\s+([A-Z][A-Za-z0-9_. '-]{1,60})", instruction or "")
    if not match:
        return ""
    value = match.group(1).strip(" .,'\"")
    value = re.split(r"\b(?:and|with|about|that|who|whose|latest|last)\b", value, maxsplit=1, flags=re.IGNORECASE)[0]
    return value.strip(" .,'\"")


def _email_query(instruction: str, sender_hint: str) -> str:
    parts = ["in:inbox"]
    if sender_hint:
        parts.append(f"from:{sender_hint}")
    if re.search(r"\b(pdf|attachment|document|docx)\b", instruction or "", re.IGNORECASE):
        parts.append("has:attachment")
    return " ".join(parts)


def _first_sentence(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    match = re.match(r"(.{1,240}?[.!?])(?:\s|$)", cleaned)
    return match.group(1) if match else cleaned[:240]


def _extract_task_lines(text: str) -> list[str]:
    tasks: list[str] = []
    for raw in re.split(r"[\n.;]+", text or ""):
        item = raw.strip(" -\t")
        if not item:
            continue
        if re.search(r"\b(add|update|change|fix|remove|create|scope|test|validate)\b", item, re.IGNORECASE):
            tasks.append(item[:180])
    return tasks[:12]


def _handoff_instruction(plan: DelegatedPlan, scope: dict[str, Any]) -> str:
    tasks = scope.get("tasks") or []
    task_text = "\n".join(f"- {task}" for task in tasks)
    return (
        "Implement the scoped delegated workflow work.\n\n"
        f"Original request:\n{plan.original_instruction}\n\n"
        f"Summary:\n{scope.get('summary', '')}\n\n"
        f"Tasks:\n{task_text}\n\n"
        "Report changed files, verification evidence, blockers, and any required user approval back to DecisionsAI."
    )


def _direct_handoff_instruction(plan: DelegatedPlan) -> str:
    return (
        "Execute this delegated project handoff from DecisionsAI.\n\n"
        f"Original request:\n{plan.original_instruction}\n\n"
        "Report changed files, verification evidence, blockers, and any required user approval back to DecisionsAI."
    )
