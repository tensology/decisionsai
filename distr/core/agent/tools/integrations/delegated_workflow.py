"""Hermes delegated workflow planning tool."""

from __future__ import annotations

import json
from typing import Optional

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class DelegatedWorkflowInput(BaseModel):
    text: str = Field(description="The full user request to compile into a delegated Orchestrator workflow plan.")
    source_surface: str = Field(default="chat", description="Where the request came from, such as telegram, desktop, browser, or chat.")
    workflow_id: Optional[int] = Field(default=None)
    run_id: Optional[int] = Field(default=None)
    step_id: Optional[int] = Field(default=None)
    ticket_id: Optional[int] = Field(default=None)
    board_id: Optional[int] = Field(default=None)
    project_id: Optional[int] = Field(default=None)
    preflight: bool = Field(default=False, description="If true, check whether required adapters/backends are ready without executing side effects.")
    execute: bool = Field(default=False, description="If true, execute the plan through available adapters and return a run report.")


class DelegatedWorkflowTool(BaseTool):
    """Create and record a Hermes delegated workflow plan from a remote instruction."""

    name: str = "delegated_workflow"
    description: str = (
        "Compile complex remote instructions into a typed Hermes delegated workflow plan. "
        "Use for Telegram/desktop requests that need email lookup, attachment/document intake, "
        "desktop/browser operations, Codex/Cursor handoff, blockers, approvals, or resumable execution. "
        "This plans and records the run; concrete tools still execute the individual steps."
    )
    args_schema: type[BaseModel] = DelegatedWorkflowInput

    def __init__(self, runner: object = None, **kwargs: object):
        super().__init__(**kwargs)
        object.__setattr__(self, "_runner", runner)

    def _run(
        self,
        text: str,
        source_surface: str = "chat",
        workflow_id: Optional[int] = None,
        run_id: Optional[int] = None,
        step_id: Optional[int] = None,
        ticket_id: Optional[int] = None,
        board_id: Optional[int] = None,
        project_id: Optional[int] = None,
        preflight: bool = False,
        execute: bool = False,
        **_: object,
    ) -> str:
        from distr.core.delegated_workflow.events import record_delegated_plan
        from distr.core.delegated_workflow.planner import plan_delegated_workflow
        from distr.core.delegated_workflow.runner import DelegatedWorkflowRunner

        plan = plan_delegated_workflow(source_surface, text)
        event_id = record_delegated_plan(
            plan,
            workflow_id=workflow_id,
            run_id=run_id,
            step_id=step_id,
            ticket_id=ticket_id,
            board_id=board_id,
            project_id=project_id,
        )
        payload = {
            "success": True,
            "event_id": event_id,
            "plan": plan.to_safe_dict(),
            "next_action": _next_action_for_plan(plan.kind),
        }
        context = {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "step_id": step_id,
            "ticket_id": ticket_id,
            "board_id": board_id,
            "project_id": project_id,
            "source_surface": source_surface,
        }
        runner = getattr(self, "_runner", None) or _default_runner()
        if preflight:
            from distr.core.delegated_workflow.events import record_delegated_preflight
            from distr.core.delegated_workflow.preflight import format_preflight_for_telegram, preflight_delegated_plan

            report = preflight_delegated_plan(plan, runner, context=context)
            preflight_event_id = record_delegated_preflight(
                plan,
                report,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=ticket_id,
                board_id=board_id,
                project_id=project_id,
            )
            payload["preflight"] = report
            payload["preflight_event_id"] = preflight_event_id
            payload["telegram_report"] = format_preflight_for_telegram(report)
            payload["success"] = bool(report.get("ready"))
            return json.dumps(payload, ensure_ascii=False)
        if execute:
            from distr.core.delegated_workflow.events import record_delegated_run_report
            from distr.core.delegated_workflow.roadblocks import format_run_report_for_telegram

            report = runner.run(plan, context=context)
            run_event_id = record_delegated_run_report(
                report,
                workflow_id=workflow_id,
                run_id=run_id,
                step_id=step_id,
                ticket_id=ticket_id,
                board_id=board_id,
                project_id=project_id,
            )
            payload["run_event_id"] = run_event_id
            payload["run_report"] = report.to_safe_dict()
            payload["telegram_report"] = format_run_report_for_telegram(report, run_id=run_event_id)
            payload["success"] = report.status == "completed"
        return json.dumps(payload, ensure_ascii=False)

    async def _arun(self, **kwargs: object) -> str:
        return self._run(**kwargs)


def _next_action_for_plan(kind: str) -> str:
    if kind == "email_document_scope":
        return "Use google_workspace to search email, then document extraction tools for attachments. Report roadblocks with the delegated workflow run context."
    if kind == "desktop_sequence":
        return "Use direct filesystem or sidecar tools first, then accessibility or browser automation fallbacks."
    if kind == "project_handoff":
        return "Use the project CLI backend handoff path for Codex/Cursor and record progress callbacks."
    return "Clarify missing account, project, app, or expected output before executing external side effects."


def _default_runner():
    from distr.core.delegated_workflow.adapters import (
        DocumentExtractorAdapter,
        DirectDesktopAdapter,
        GoogleEmailAdapter,
        PlaywrightBrowserAdapter,
        ProjectCliDispatcher,
    )
    from distr.core.delegated_workflow.runner import DelegatedWorkflowRunner

    return DelegatedWorkflowRunner(
        email_adapter=GoogleEmailAdapter(),
        document_adapter=DocumentExtractorAdapter(),
        desktop_adapter=DirectDesktopAdapter(),
        browser_adapter=PlaywrightBrowserAdapter(),
        project_dispatcher=ProjectCliDispatcher(),
    )
