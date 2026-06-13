"""Conservative deterministic planner for delegated remote instructions."""

from __future__ import annotations

import re

from .models import DelegatedPlan, DelegatedStep


_EMAIL_TERMS = re.compile(r"\b(email|gmail|inbox|mail)\b", re.IGNORECASE)
_DOCUMENT_TERMS = re.compile(r"\b(pdf|document|docx|attachment|file|changes)\b", re.IGNORECASE)
_SCOPE_TERMS = re.compile(r"\b(scope|plan|prep|prepare|summari[sz]e|what needs to be executed)\b", re.IGNORECASE)
_DESKTOP_TERMS = re.compile(r"\b(copy|paste|cut|open|sublime|downloads?|save|keyboard|mouse|click)\b", re.IGNORECASE)
_HANDOFF_TERMS = re.compile(r"\b(codex|cursor|implement|development|handoff|report back)\b", re.IGNORECASE)
_BROWSER_TERMS = re.compile(r"\b(browser|website|web\s*site|url|playwright|browseruse|browser use|chrome|brave|safari|web page|screenshot|localhost)\b|https?://|file://", re.IGNORECASE)


def _target_backend(text: str) -> str:
    lowered = text.lower()
    if "cursor" in lowered:
        return "cursor"
    if "codex" in lowered or "codecs" in lowered:
        return "codex"
    return ""


def _email_document_plan(source_surface: str, instruction: str) -> DelegatedPlan:
    backend = _target_backend(instruction)
    steps = [
        DelegatedStep(
            action="resolve_contact",
            preferred_route="orchestrator_memory",
            fallback_routes=["user_clarification"],
            description="Resolve the named sender/contact from account memory and recent history.",
            verifies=["contact_candidate_selected"],
        ),
        DelegatedStep(
            action="search_email",
            preferred_route="google_workspace",
            fallback_routes=["browser_automation", "desktop_accessibility"],
            description="Search the connected email account for the latest relevant message.",
            verifies=["email_message_id_found"],
        ),
        DelegatedStep(
            action="download_attachments",
            preferred_route="google_workspace",
            fallback_routes=["browser_automation", "desktop_accessibility"],
            description="Download relevant attachments into a controlled DecisionsAI intake folder.",
            verifies=["attachment_file_exists"],
        ),
        DelegatedStep(
            action="extract_document",
            preferred_route="document_extractor",
            fallback_routes=["ocr_vision", "user_clarification"],
            description="Extract text and metadata from PDFs, DOCX files, images, or archives.",
            verifies=["document_text_available"],
        ),
        DelegatedStep(
            action="scope_execution",
            preferred_route="workflow_planner",
            fallback_routes=["project_cli_backend"],
            description="Create an execution scope with assumptions, risks, blockers, and next actions.",
            verifies=["scope_summary_created"],
        ),
    ]
    if backend:
        steps.append(
            DelegatedStep(
                action="dispatch_project_handoff",
                preferred_route="project_cli_backend",
                fallback_routes=["workflow_ticket", "user_clarification"],
                description=f"Send approved implementation work to {backend}.",
                params={"backend": backend},
                verifies=["handoff_event_recorded"],
            )
        )
    return DelegatedPlan(
        kind="email_document_scope",
        source_surface=source_surface,
        original_instruction=instruction,
        steps=steps,
        requires_approval_before=["send_outbound_message", "modify_project_files"],
        target_backend=backend,
        confidence=0.86,
    )


def _desktop_sequence_plan(source_surface: str, instruction: str) -> DelegatedPlan:
    return DelegatedPlan(
        kind="desktop_sequence",
        source_surface=source_surface,
        original_instruction=instruction,
        steps=[
            DelegatedStep("capture_source_content", "clipboard", ["screen_vision"], "Capture the text or file content the user referred to.", verifies=["source_content_available"]),
            DelegatedStep("set_clipboard", "sidecar", ["desktop_accessibility"], "Set clipboard explicitly instead of relying on fragile mouse selection.", verifies=["clipboard_matches_source"]),
            DelegatedStep("launch_or_focus_app", "sidecar", ["desktop_accessibility", "keyboard_shortcut"], "Open or focus the target native app.", verifies=["target_window_focused"]),
            DelegatedStep("create_or_open_file", "filesystem", ["desktop_accessibility", "browser_automation"], "Create the destination file directly when possible.", verifies=["destination_ready"]),
            DelegatedStep("write_text", "filesystem", ["clipboard", "desktop_accessibility"], "Write or paste the captured content.", verifies=["destination_contains_text"]),
            DelegatedStep("verify_result", "filesystem", ["screen_vision"], "Verify the final file or visible UI state.", verifies=["result_verified"]),
        ],
        requires_approval_before=["overwrite_existing_file"],
        confidence=0.78,
    )


def _project_handoff_plan(source_surface: str, instruction: str) -> DelegatedPlan:
    backend = _target_backend(instruction) or "codex"
    return DelegatedPlan(
        kind="project_handoff",
        source_surface=source_surface,
        original_instruction=instruction,
        steps=[
            DelegatedStep("resolve_project_context", "orchestrator_memory", ["user_clarification"], "Find the active project, ticket, or workflow context.", verifies=["project_context_found"]),
            DelegatedStep("prepare_handoff_packet", "orchestrator", ["workflow_ticket"], "Build a redacted Decisions-to-worker packet.", verifies=["handoff_packet_created"]),
            DelegatedStep(
                "dispatch_project_handoff",
                "project_cli_backend",
                ["workflow_ticket", "user_clarification"],
                f"Send the work to {backend} and record progress callbacks.",
                params={"backend": backend},
                verifies=["handoff_event_recorded"],
            ),
        ],
        requires_approval_before=["modify_project_files"],
        target_backend=backend,
        confidence=0.82,
    )


def _browser_workflow_plan(source_surface: str, instruction: str) -> DelegatedPlan:
    return DelegatedPlan(
        kind="browser_workflow",
        source_surface=source_surface,
        original_instruction=instruction,
        steps=[
            DelegatedStep("prepare_browser_task", "orchestrator_routing", ["user_clarification"], "Resolve URL, browser state, credentials, and expected result.", verifies=["browser_task_actionable"]),
            DelegatedStep("execute_browser_actions", "playwright", ["browser_use", "desktop_accessibility"], "Run browser automation through Playwright or the browser-use fallback.", verifies=["browser_actions_completed"]),
            DelegatedStep("verify_browser_result", "browser_snapshot", ["vision_llm", "console_log"], "Capture screenshot and console evidence for the final browser state.", verifies=["browser_result_verified"]),
        ],
        requires_approval_before=["submit_forms", "send_outbound_message"],
        confidence=0.76,
    )


def _general_plan(source_surface: str, instruction: str) -> DelegatedPlan:
    return DelegatedPlan(
        kind="general_delegated_request",
        source_surface=source_surface,
        original_instruction=instruction,
        steps=[
            DelegatedStep("clarify_goal", "orchestrator_memory", ["user_clarification"], "Resolve the target account, project, app, and expected output.", verifies=["goal_is_actionable"]),
            DelegatedStep("choose_execution_route", "orchestrator_routing", ["user_clarification"], "Select API, browser, desktop, or project handoff route.", verifies=["route_selected"]),
        ],
        requires_approval_before=["external_side_effect"],
        confidence=0.55,
    )


def plan_delegated_workflow(source_surface: str, instruction: str) -> DelegatedPlan:
    """Compile a remote/desktop instruction into a typed Hermes execution plan."""
    text = instruction or ""
    source = (source_surface or "unknown").strip().lower() or "unknown"
    has_email_doc_scope = bool(_EMAIL_TERMS.search(text) and _DOCUMENT_TERMS.search(text) and _SCOPE_TERMS.search(text))
    if has_email_doc_scope:
        return _email_document_plan(source, text)
    if _BROWSER_TERMS.search(text):
        return _browser_workflow_plan(source, text)
    if _HANDOFF_TERMS.search(text) and _target_backend(text):
        return _project_handoff_plan(source, text)
    if _DESKTOP_TERMS.search(text):
        return _desktop_sequence_plan(source, text)
    return _general_plan(source, text)
