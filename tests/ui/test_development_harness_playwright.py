"""Focused operability checks for the Development harness UI."""

from __future__ import annotations

import os
TEST_BASE_URL = os.environ.get("DEVELOPMENT_TEST_URL", "http://127.0.0.1:8765")

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e_playwright
URL = TEST_BASE_URL + "/development/threads/17/"


def _open_details(page: Page) -> None:
    page.locator("#thread-more-button").click()
    page.locator("#thread-menu-details").click()


def _open_thread_action(page: Page, action: str, chat_id: int = 17) -> None:
    page.locator(f'[data-chat-id="{chat_id}"]').first.click(button="right")
    page.get_by_role("menuitem", name=action, exact=True).click()


def _install_api(
    page: Page,
    requests: list[tuple[str, str]],
    *,
    pending_interaction: bool = False,
    live_activity: bool = False,
    autonomy_level: str = "full",
    unassigned_recent: bool = False,
    run_status: str | None = None,
    running_timer: bool = False,
    board_linked_projectless: bool = False,
    complete_after_refresh: bool = False,
    complete_after_cancel: bool = False,
    current_board_linked: bool = False,
    direct_execution: bool = False,
    assistant_markdown: bool = False,
    completed_activity: bool = False,
    durable_url_activity: bool = False,
    assistant_missing_duration: bool = False,
    native_stream: bool = False,
    message_validation_error: bool = False,
    direct_complete_after_refresh: bool = False,
    terminal_session: bool = True,
    second_terminal_session: bool = False,
    shell_terminal_session: bool = False,
    discovered_terminal_session: bool = False,
    discovered_terminal_parent_pid: int = 1,
    unavailable_board_id: int | None = None,
    submitted_payloads: list[dict] | None = None,
    message_skills: list[str] | None = None,
    legacy_skill_message: bool = False,
    lane_move_notice: bool = False,
    stale_workflow_on_direct_thread: bool = False,
    url_activity: bool = False,
    long_whatsapp_thread: bool = False,
    automation_thread_chat_id: int | None = None,
    startup_instructions: str = "npm run dev",
    projects_include_startup_instructions: bool = True,
    terminal_start_success: bool = True,
    terminal_status_sessions: list[dict] | None = None,
    terminal_status_available: bool = True,
    terminal_command: str = "npm run dev",
    unified_direct_run: bool = False,
) -> None:
    projects = [
        {"id": 7, "name": "DecisionsAI", "folder_location": "/tmp/decisions", "in_use": True, "kanban_board_id": 70, "startup_instructions": startup_instructions},
        {"id": 8, "name": "Empty project", "folder_location": "", "in_use": False, "kanban_board_id": None, "startup_instructions": ""},
    ]
    if not projects_include_startup_instructions:
        for project in projects:
            project.pop("startup_instructions", None)
    automations: list[dict] = []
    imported_sources: set[str] = set()
    queued_commands: list[dict] = []
    deleted_chat_ids: set[int] = set()
    discovered_sessions = ([{"process_id": "discovered:202", "pid": 202, "ppid": discovered_terminal_parent_pid, "command": "python3 -m http.server 43117", "cwd": "/tmp/decisions", "purpose": "discovered"}] if discovered_terminal_session else [])
    startup_sessions = ([{"process_id": "term-1", "pid": 101, "command": terminal_command, "cwd": "/tmp/decisions", "purpose": "startup", "memory_bytes": 134217728}] if terminal_session else [])
    if second_terminal_session:
        startup_sessions.append({"process_id": "term-2", "pid": 102, "command": "npm run worker", "cwd": "/tmp/decisions", "purpose": "startup", "memory_bytes": 67108864})
    shell_sessions = ([{"process_id": "shell-303", "pid": 303, "command": "zsh -il", "cwd": "/tmp/decisions", "purpose": "cli_shell", "memory_bytes": 67108864}] if shell_terminal_session else [])
    active_run_reads = 0
    workflow_cancelled = False
    direct_run = {
        "id": "direct-91",
        "job_id": "direct-91",
        "execution_session_id": 91,
        "status": run_status or ("running" if live_activity else "completed"),
        "backend_id": "codex",
        "model": "auto",
        "activity": "Updating project files." if live_activity else "Implementation complete.",
        "completed_at": "2026-08-27T10:12:34Z" if not live_activity else None,
        "direct": True,
        "changes": {
            "files": [
                {"path": "index.html", "status": " M", "additions": 7, "deletions": 1, "reversible": True},
                {"path": "src/menu.js", "status": " M", "additions": 4, "deletions": 2, "reversible": True},
            ],
            "additions": 11,
            "deletions": 3,
            "reversible": True,
            "undone": False,
        },
        **({
            "runtime_id": "native",
            "activity_status": "updating",
            "streamed_output": "**Draft response** from the native model.",
        } if native_stream else {}),
    } if direct_execution else {}
    chat = {
        "id": 17,
        "title": "Implement ticket DEV-42",
        "project_id": 7,
        "provider": "OpenAI",
        "model_name": "gpt-test",
        "route_mode": "auto",
        "execution_profile": "code",
        "autonomy_level": autonomy_level,
        "development": ({"execution": direct_run, "ticket_id": 42} if direct_execution else True),
        "development_workflow_id": 44 if (not direct_execution or stale_workflow_on_direct_thread) else None,
        "development_execution": direct_run,
        "development_ticket_id": 42,
        "development_board_key": "decisions:70" if current_board_linked else None,
        "development_board_ticket_key": "decisions:42" if current_board_linked else None,
        "development_board_ticket_title": "DEV-42" if current_board_linked else None,
        "pinned": False,
        "permission_profile": {"mode": "standard"},
        "remote_continuation": False,
        "archived": False,
        "messages": ([
            {"role": "user", "content": "Implement DEV-42", "chat_row_id": 19},
            {
                "role": "workflow",
                "content": "Implementation started",
                "workflow_event": {"phase": "Develop", "summary": "Implementation started", "status": "running"},
            },
            {
                "role": "tool",
                "tool_event": {
                    "tool_name": "Terminal",
                    "summary": "Running the focused test suite",
                    "status": "running",
                },
            },
            {"role": "assistant", "content": "I found the affected route and I am verifying the change."},
        ] if live_activity else [
            {"role": "user", "content": "Implement DEV-42", "chat_row_id": 19},
            *([{
                "role": "tool",
                "tool_event": {
                    "tool_name": "open_page",
                    "title": f"Opened URL: {TEST_BASE_URL}/chat/",
                    "summary": f"Opened URL: {TEST_BASE_URL}/chat/",
                    "status": "completed",
                },
            }] if url_activity else []),
            *([{"role": "assistant", "content": "## Result\n\n**Implemented** the fix.\n\nPreview: http://localhost:5174/\n\n- Updated `index.html`\n- Updated `src/menu.js`\n\n| File | Change |\n| --- | --- |\n| `index.html` | Added navigation |\n| `src/menu.js` | Synced state |\n\n```diff\n- const ready = false;\n+ const ready = true;\n```", "timestamp": "2026-08-27T10:12:34Z", "turn_started_at": "2026-08-27T10:10:00Z", "turn_duration_seconds": 154, "chat_row_id": 19}] if assistant_markdown else []),
            *([{"role": "assistant", "content": "Open `http://localhost:5174/` to preview the project.", "timestamp": "2026-08-27T10:15:00Z"}] if assistant_missing_duration else []),
            *([{"role": "assistant", "content": "Ticket #42 \"DEV-42\" on board \"Decisions delivery\" was moved from \"In progress\" to lane \"QA\".", "timestamp": "2026-08-27T10:16:00Z"}] if lane_move_notice else []),
        ]),
        "active_turn": ({
            "turn_id": 19,
            "status": "running",
            "active": True,
            "events": [
                {"event_id": "read-1", "event_type": "tool_completed", "status": "completed", "title": "Read File", "summary": "Read styles.css lines 1-16.", "metadata": {"tool_name": "read_file"}},
                {"event_id": "edit-1", "event_type": "tool_completed", "status": "completed", "title": "Replace Text", "summary": "Updated styles.css with 1 replacement.", "metadata": {"tool_name": "replace_text", "files": [{"path": "styles.css", "additions": 3, "deletions": 1}]}},
                {"event_id": "image-1", "event_type": "tool_completed", "status": "completed", "title": "View Image", "summary": "Viewed the browser screenshot.", "metadata": {"tool_name": "view_image"}},
                {"event_id": "command-1", "event_type": "tool_started", "status": "running", "title": "Run Command", "summary": "Running the focused tests.", "metadata": {"tool_name": "run_command", "command": "python -m pytest -q tests/ui/test_menu.py"}},
            ],
        } if live_activity else None),
        "turns": ([{
            "turn_id": 19,
            "status": "completed",
            "active": False,
            "events": [
                *([{"event_id": "browser-url-1", "event_type": "tool_completed", "status": "completed", "title": "Browser Check", "summary": f"Opened URL: {TEST_BASE_URL}/chat/", "metadata": {"tool_name": "smart_open"}}] if durable_url_activity else []),
                *([
                {"event_id": "agent-1", "event_type": "tool_completed", "status": "completed", "title": "Working", "summary": "Implemented the fix.", "metadata": {"tool_name": "development_agent"}},
                {"event_id": "read-1", "event_type": "tool_completed", "status": "completed", "title": "Read File", "summary": "Read the menu implementation.", "detail": "src/menu.js", "metadata": {"tool_name": "read_file"}},
                {"event_id": "edit-1", "event_type": "tool_completed", "status": "completed", "title": "Replace Text", "summary": "Updated the menu state.", "detail": "src/menu.js", "metadata": {"tool_name": "replace_text", "files": [{"path": "src/menu.js", "additions": 4, "deletions": 2}]}},
                {"event_id": "test-1", "event_type": "tool_completed", "status": "completed", "title": "Run Command", "summary": "Focused tests passed.", "metadata": {"tool_name": "run_command", "command": "python -m pytest -q tests/ui/test_menu.py"}},
                {"event_id": "command-1", "event_type": "tool_completed", "status": "completed", "title": "Run Command", "summary": "Checked the working tree.", "metadata": {"tool_name": "run_command", "command": "git status --short"}},
                {"event_id": "browser-1", "event_type": "tool_completed", "status": "completed", "title": "Browser Check", "summary": "Checked the responsive result.", "detail": "Desktop and compact widths", "metadata": {"tool_name": "browser_screenshot"}},
                {"event_id": "done-1", "event_type": "turn_completed", "status": "completed", "title": "Answer ready", "summary": "Implemented the fix."},
                ] if completed_activity else []),
            ],
        }] if (completed_activity or durable_url_activity) else []),
    }
    if message_skills and chat["messages"]:
        chat["messages"][0]["skills"] = list(message_skills)
    if legacy_skill_message and chat["messages"]:
        chat["messages"][0]["content"] = (
            "Use these skills and tell me what to do\n\nFocused context for this turn:\n\n"
            "[Skill: Accessibility]\nUse the installed skill \"accessibility\" for this turn. "
            "Read it with read_harness_skill before acting, then follow its instructions for this request."
        )
    workflow = {
        "id": 44,
        "name": "Development execution",
        "chat_id": None,
        "steps": [{"id": 1, "name": "Inspect", "instruction": "Inspect the ticket", "action_type": "agent_instruction", "config": {"skills": ["browser-qa"], "expected_outputs": ["A scoped implementation plan"]}, "status": "pending"}],
        "run_settings": {"studio": {"ticket_id": 42}},
    }
    workflow_rows = [workflow]
    thread_time = {
        "chat_id": 17,
        "seconds": 5 if running_timer else 125,
        "accumulated_seconds": 0 if running_timer else 125,
        "paused": not running_timer,
        "running": running_timer,
        "started_at": "2026-08-25T13:00:00" if running_timer else None,
    }
    local_board = {
        "id": 70,
        "name": "Development",
        "source": "database",
        "lanes": [
            {"id": 701, "name": "In progress", "tickets": [{"id": 42, "title": "DEV-42", "description": "Implement the development harness", "linked_project_id": 7, "priority": "high", "complexity": "moderate", "source_chat_id": 17}]},
            {"id": 702, "name": "QA", "tickets": []},
        ],
    }
    jira_board = {
        "name": "Jira engineering",
        "cache_ready": True,
        "lanes": [
            {"id": "In review", "name": "In review", "tickets": [{
                "id": "DEV-99",
                "title": "Jira visual review",
                "description": "Review the custom composer",
                "attachments": [
                    {"id": "att-1", "name": "composer.png", "mime_type": "image/png", "preview_url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="},
                    {"id": "att-2", "name": "requirements.pdf", "mime_type": "application/pdf"},
                ],
                "comments": [{"author": "Paul", "created_at": "2026-08-27T10:00:00Z", "body": "Keep the composer compact."}],
            }]},
            {"id": "QA", "name": "QA", "tickets": []},
        ],
    }
    trello_board = {
        "name": "Trello launch",
        "cache_ready": True,
        "lanes": [
            {"id": "list-doing", "name": "Doing", "tickets": [{"id": "card-9", "name": "Trello launch card", "desc": "Prepare launch"}]},
            {"id": "list-done", "name": "Done", "tickets": []},
        ],
    }

    def handler(route):
        nonlocal active_run_reads, workflow_cancelled
        request = route.request
        path = urlparse(request.url).path
        requests.append((request.method, path))
        if path == "/api/events/stream":
            route.continue_()
            return
        payload = None
        if path == "/api/workflows/studio/reports":
            payload = {"items": []}
        elif path == "/api/workflows/studio/plan-workspaces" and request.method == "GET":
            payload = {"items": []}
        elif path == "/api/workflows/studio/attachments":
            upload_index = requests.count(("POST", path))
            name = "reference.png" if upload_index == 1 else "notes.md"
            payload = {"name": name, "path": f"/tmp/decisions-development-attachments/{name}", "mime_type": "image/png" if name.endswith(".png") else "text/markdown", "size": 8}
        elif path == "/api/browse-folder":
            payload = {"path": "/tmp/picked-repository"}
        elif path == "/api/projects":
            if request.method == "POST":
                body = json.loads(request.post_data or "{}")
                created = {"id": 9, "name": body.get("name", "New project"), "folder_location": body.get("folder_location", ""), "in_use": False}
                projects.append(created)
                payload = {"id": 9, "success": True}
            else:
                payload = projects
        elif path == "/api/projects/terminal-status":
            if not terminal_status_available:
                route.fulfill(status=404, content_type="application/json", body='{"detail":"Not available"}')
                return
            managed_status_sessions = terminal_status_sessions if terminal_status_sessions is not None else [*startup_sessions, *shell_sessions]
            payload = {
                "projects": {
                    "7": {
                        "running": bool(managed_status_sessions),
                        "startup_count": sum(1 for session in managed_status_sessions if session.get("purpose") == "startup"),
                        "shell_count": sum(1 for session in managed_status_sessions if session.get("purpose") == "cli_shell"),
                        "sessions": list(managed_status_sessions),
                    },
                    "8": {"running": False, "startup_count": 0, "shell_count": 0, "sessions": []},
                }
            }
        elif path == "/api/tickets/boards":
            if request.method == "POST":
                payload = {"success": True, "id": 90, "project_id": 9}
            else:
                payload = [
                    {"id": 70, "name": "Decisions delivery", "source": "database", "default_project_id": 7, "folder_location": "/tmp/decisions", "startup_instructions": startup_instructions},
                    {"id": 80, "name": "Empty delivery", "source": "database", "default_project_id": 8, "folder_location": "", "startup_instructions": ""},
                ]
        elif path == "/api/tickets/external-boards":
            payload = {
                "cache_ready": True,
                "jira": [{"id": "jira-1", "name": "Jira engineering", "default_project_id": 7}],
                "trello": [{"id": "trello-1", "name": "Trello launch"}],
            }
        elif path == "/api/projects/7":
            if request.method in {"PATCH", "PUT"}:
                body = json.loads(request.post_data or "{}")
                projects[0].update(body)
                payload = projects[0]
            else:
                payload = {**projects[0], "startup_instructions": projects[0].get("startup_instructions", startup_instructions), "context_items": [{"id": 31, "title": "Architecture constraints", "content": "Keep the DecisionsAI shell."}], "files": [{"id": 41, "filename": "brief.md", "description": "Current development brief", "file_path": "/tmp/decisions/brief.md"}]}
        elif path == "/api/projects/8":
            if request.method == "DELETE":
                projects[:] = [project for project in projects if project["id"] != 8]
                payload = {"success": True}
            else:
                payload = {**projects[1], "startup_instructions": "", "context_items": [], "files": []}
        elif path in {"/api/projects/8/startup-sessions", "/api/projects/8/shell-terminal"}:
            payload = {"sessions": []}
        elif path == "/api/chats":
            recent = ([{
                "id": 19,
                "title": "Open-ended development",
                "project_id": None,
                "development_board_key": "decisions:70" if board_linked_projectless else None,
                "development": True,
                "archived": False,
            }] if unassigned_recent else [])
            payload = {
                "last_chat_id": 1,
                "chats": [
                    {"id": 1, "title": "Ordinary voice chat", "project_id": 7, "development": False},
                    *([{key: value for key, value in chat.items() if key != "messages"}] if 17 not in deleted_chat_ids else []),
                    *recent,
                ],
            }
        elif path == "/api/tickets/boards/70" and request.method == "PUT":
            body = json.loads(request.post_data or "{}")
            payload = {"success": True, "default_workflow_id": body.get("default_workflow_id")}
        elif path == "/api/tickets/boards/70" and request.method == "DELETE":
            payload = {"success": True, "repository_deleted": "delete_repository=true" in request.url}
        elif path == "/api/tickets/boards/70":
            payload = local_board
        elif path == "/api/tickets/tickets/42/move":
            body = json.loads(request.post_data or "{}")
            ticket = local_board["lanes"][0]["tickets"].pop()
            target = next(lane for lane in local_board["lanes"] if lane["id"] == body["lane_id"])
            target["tickets"].append(ticket)
            payload = {"success": True}
        elif path == "/api/tickets/tickets/42" and request.method == "DELETE":
            for lane in local_board["lanes"]:
                lane["tickets"] = [ticket for ticket in lane["tickets"] if ticket["id"] != 42]
            payload = {"success": True, "deleted_thread_id": 17 if "delete_thread=true" in request.url else None}
        elif path == "/api/tickets/tickets/42" and request.method == "PUT":
            body = json.loads(request.post_data or "{}")
            ticket = next(ticket for lane in local_board["lanes"] for ticket in lane["tickets"] if ticket["id"] == 42)
            ticket.update(body)
            payload = ticket
        elif path == "/api/tickets/boards/80":
            if unavailable_board_id == 80:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"Kanban unavailable"}')
                return
            payload = {"id": 80, "name": "Empty delivery", "source": "database", "lanes": []}
        elif path == "/api/tickets/external-boards/jira/jira-1/move-ticket":
            body = json.loads(request.post_data or "{}")
            ticket = next((lane["tickets"].pop(index) for lane in jira_board["lanes"] for index, item in enumerate(lane["tickets"]) if item["id"] == body["ticket_id"]), None)
            if ticket:
                next(lane for lane in jira_board["lanes"] if lane["id"] == body["target_lane_id"])["tickets"].append(ticket)
            payload = {"success": True, "cache_updated": True}
        elif path == "/api/tickets/thread-draft":
            payload = {
                "title": "Jira visual review",
                "description": "Review the custom composer",
                "prompt": "Jira visual review\n\nReview the custom composer",
                "attachments": [
                    {"name": "composer.png", "path": "/tmp/decisions-development-attachments/composer.png", "mime_type": "image/png", "size": 8},
                    {"name": "requirements.pdf", "path": "/tmp/decisions-development-attachments/requirements.pdf", "mime_type": "application/pdf", "size": 12},
                    {"name": "ticket-comments.txt", "path": "/tmp/decisions-development-attachments/ticket-comments.txt", "mime_type": "text/plain", "size": 32},
                ],
                "comments_count": 1,
                "warnings": [],
            }
        elif path == "/api/tickets/external-boards/trello/trello-1/move-ticket":
            body = json.loads(request.post_data or "{}")
            ticket = next((lane["tickets"].pop(index) for lane in trello_board["lanes"] for index, item in enumerate(lane["tickets"]) if item["id"] == body["ticket_id"]), None)
            if ticket:
                next(lane for lane in trello_board["lanes"] if lane["id"] == body["target_lane_id"])["tickets"].append(ticket)
            payload = {"success": True, "cache_updated": True}
        elif path == "/api/tickets/external-boards/jira/jira-1":
            payload = jira_board
        elif path == "/api/tickets/external-boards/trello/trello-1":
            payload = trello_board
        elif path == "/api/tickets/external-boards/trello/trello-1/register":
            payload = {"success": True}
        elif path == "/api/chats/17" and request.method == "GET":
            payload = chat
        elif path == "/api/workflows/studio/tasks/17/time":
            if request.method == "PATCH":
                body = json.loads(request.post_data or "{}")
                thread_time.update({"seconds": body.get("seconds", 0), "accumulated_seconds": body.get("seconds", 0)})
            payload = thread_time
        elif path == "/api/workflows/studio/tasks/17/execution/changes/undo":
            direct_run["changes"]["undone"] = True
            payload = {"ok": True, "changes": direct_run["changes"]}
        elif path == "/api/workflows/studio/tasks/17/execution/changes":
            if not direct_run.get("changes"):
                payload = {"files": [], "review_files": [], "diff": ""}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))
                return
            index_diff = "diff --git a/index.html b/index.html\n--- a/index.html\n+++ b/index.html\n@@ -1,2 +1,2 @@\n-<h1>Menu</h1>\n+<h1 class=\"menu\">Menu</h1>\n <nav>Home</nav>"
            menu_diff = "diff --git a/src/menu.js b/src/menu.js\n--- a/src/menu.js\n+++ b/src/menu.js\n@@ -8,2 +8,2 @@\n-const ready = false;\n+const ready = true;\n renderMenu();"
            payload = {
                **direct_run.get("changes", {}),
                "diff": f"{index_diff}\n{menu_diff}",
                "review_files": [
                    {**direct_run["changes"]["files"][0], "diff": index_diff, "binary": False, "hunks": [{"header": "@@ -1,2 +1,2 @@", "old_start": 1, "new_start": 1, "lines": [{"kind": "deletion", "old_line": 1, "new_line": None, "content": "<h1>Menu</h1>"}, {"kind": "addition", "old_line": None, "new_line": 1, "content": "<h1 class=\"menu\">Menu</h1>"}, {"kind": "context", "old_line": 2, "new_line": 2, "content": "<nav>Home</nav>"}]}]},
                    {**direct_run["changes"]["files"][1], "diff": menu_diff, "binary": False, "hunks": [{"header": "@@ -8,2 +8,2 @@", "old_start": 8, "new_start": 8, "lines": [{"kind": "deletion", "old_line": 8, "new_line": None, "content": "const ready = false;"}, {"kind": "addition", "old_line": None, "new_line": 8, "content": "const ready = true;"}, {"kind": "context", "old_line": 9, "new_line": 9, "content": "renderMenu();"}]}]},
                ],
            }
        elif path == "/api/workflows/studio/tasks/17/execution":
            if direct_complete_after_refresh and active_run_reads > 1:
                direct_run.update({"status": "completed", "activity": "Instruction completed."})
                chat["active_turn"] = None
                if not any(message.get("content") == "Instruction completed." for message in chat["messages"]):
                    chat["messages"].append({"role": "assistant", "content": "Instruction completed."})
            payload = direct_run or {"direct": True}
        elif path == "/api/workflows/studio/tasks/17/execution/stop":
            direct_run.update({"status": "cancelled", "activity": "Stopped by the user."})
            payload = {**direct_run, "stopped": True}
        elif path == "/api/workflows/studio/tasks/17/time/play":
            thread_time.update({"paused": False, "running": True, "started_at": None})
            payload = thread_time
        elif path == "/api/workflows/studio/tasks/17/time/pause":
            thread_time.update({"paused": True, "running": False, "started_at": None})
            payload = thread_time
        elif path == "/api/workflows/studio/tasks/17/clear-context":
            payload = {"chat_id": 17, "cleared": True, "boundary_chat_row_id": 19}
        elif path == "/api/projects/7/startup-terminals/start":
            if terminal_start_success:
                startup_sessions[:] = [{"process_id": "term-1", "pid": 101, "command": "npm run dev", "cwd": "/tmp/decisions", "purpose": "startup", "memory_bytes": 134217728}]
                payload = {"success": True, "message": "Started 1 terminal", "started": 1, "sessions": list(startup_sessions)}
            else:
                payload = {"success": False, "message": "Terminal launch failed", "started": 0, "sessions": []}
        elif path == "/api/projects/7/startup-sessions":
            managed_startup_sessions = terminal_status_sessions if terminal_status_sessions is not None else startup_sessions
            payload = {"sessions": [session for session in managed_startup_sessions if session.get("purpose") == "startup"] + discovered_sessions}
        elif path == "/api/projects/7/shell-terminal":
            payload = {"sessions": list(shell_sessions)}
        elif path == "/api/projects/7/startup-terminals/stop":
            stopped_count = len(startup_sessions)
            startup_sessions.clear()
            payload = {"success": True, "message": f"Stopped {stopped_count} terminal", "stopped": stopped_count}
        elif path == "/api/projects/kill-terminal":
            killed_process_id = json.loads(request.post_data or "{}").get("process_id", "")
            if killed_process_id.startswith("discovered:"):
                discovered_sessions.clear()
            startup_sessions[:] = [session for session in startup_sessions if session["process_id"] != killed_process_id]
            shell_sessions[:] = [session for session in shell_sessions if session["process_id"] != killed_process_id]
            payload = {"success": True}
        elif path == "/api/chats/17" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            chat.update(body)
            payload = chat
        elif path == "/api/chats/17" and request.method == "DELETE":
            payload = {"message": "Chat deleted successfully"}
        elif path == "/api/chats/17/fork":
            payload = {"ok": True, "id": 18, "checkpoint": {"summary": "Fork checkpoint"}}
        elif path == "/api/workflows/skills":
            payload = {"skills": [
                {"id": "browser-qa", "name": "Browser QA", "description": "Verify browser behavior with Playwright."},
                {"id": "accessibility", "name": "Accessibility", "description": "Audit accessible interaction and semantics."},
                {"id": "humanizer", "name": "Humanizer", "description": "Improve natural language output."},
            ], "count": 3}
        elif path == "/api/workflows":
            payload = [{**item, "step_count": len(item.get("steps") or [])} for item in workflow_rows]
        elif path == "/api/workflows/plan":
            body = json.loads(request.post_data or "{}")
            created = {"id": 45, "name": "Ticket delivery loop", "description": body.get("instruction", ""), "steps": [{"id": 451, "position": 1, "name": "Implement", "instruction": "Implement the ticket"}, {"id": 452, "position": 2, "name": "Verify", "instruction": "Run end-to-end verification"}]}
            workflow_rows.append(created)
            payload = created
        elif path == "/api/workflows/44" and request.method == "PATCH":
            workflow.update(json.loads(request.post_data or "{}"))
            payload = {"success": True}
        elif path == "/api/workflows/44/generate-steps" and request.method == "POST":
            workflow["steps"] = [
                {"id": 1, "position": 0, "name": "Inspect", "instruction": "Inspect the ticket", "action_type": "agent_instruction", "config": {"skills": ["browser-qa"], "expected_outputs": ["A scoped implementation plan"]}, "status": "pending"},
                {"id": 2, "position": 1, "name": "Security review", "instruction": "Review the implementation for security regressions", "action_type": "agent_instruction", "config": {"skills": ["accessibility"], "expected_outputs": ["A security verdict"]}, "status": "pending"},
            ]
            payload = {"steps": workflow["steps"]}
        elif path == "/api/workflows/44" and request.method == "DELETE":
            workflow_rows[:] = [item for item in workflow_rows if item["id"] != 44]
            payload = {"success": True}
        elif path == "/api/workflows/44":
            payload = workflow
        elif path == "/api/workflows/44/steps" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            created_step = {"id": max([int(item["id"]) for item in workflow["steps"]] + [0]) + 1, "status": "pending", **body}
            workflow["steps"].append(created_step)
            payload = workflow
        elif path == "/api/workflows/44/steps/reorder" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            positions = {int(step_id): index for index, step_id in enumerate(body.get("step_ids") or [])}
            workflow["steps"].sort(key=lambda item: positions.get(int(item["id"]), 999))
            for index, item in enumerate(workflow["steps"]):
                item["position"] = index
            payload = {"success": True}
        elif re.fullmatch(r"/api/workflows/44/steps/\d+", path) and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            step_id = int(path.rsplit("/", 1)[-1])
            next(item for item in workflow["steps"] if int(item["id"]) == step_id).update(body)
            payload = {"success": True}
        elif re.fullmatch(r"/api/workflows/44/steps/\d+", path) and request.method == "DELETE":
            step_id = int(path.rsplit("/", 1)[-1])
            workflow["steps"][:] = [item for item in workflow["steps"] if int(item["id"]) != step_id]
            payload = {"success": True}
        elif path == "/api/workflows/44/duplicate" and request.method == "POST":
            payload = {**workflow, "id": 46, "name": "Development execution (copy)"}
        elif path == "/api/workflows/44/workspace-memory":
            payload = {"handoff_preview": "Inspect, implement, validate, and retain corrections.", "step_routing_table": [{"step_id": 1, "step_name": "Inspect", "route": "Automatic"}]}
        elif path == "/api/workflows/45":
            payload = workflow_rows[-1]
        elif path == "/api/workflows/active-runs":
            active_run_reads += 1
            payload = ([{
                "id": "development:17:direct-91",
                "execution_kind": "development",
                "chat_id": 17,
                "status": "waiting",
                "ticket_id": 42,
                "ticket_title": "DEV-42",
                "project_id": 7,
                "project_name": "DecisionsAI",
                "open_url": "/development/threads/17/",
                "related_ticket_url": "/kanban/?ticket_id=42",
                "cancellation_target": {"kind": "development", "url": "/api/workflows/studio/tasks/17/execution/stop"},
            }] if unified_direct_run else [{
                "id": 99,
                "execution_kind": "workflow",
                "workflow_id": 44,
                "status": run_status or ("running" if live_activity else "waiting"),
                "steerable": True,
                "execution_session_id": "terminal-42" if live_activity else None,
                "runtime_snapshot": {"urls": [TEST_BASE_URL + "/workflows/"]} if live_activity else None,
                "ticket_id": 42,
                "ticket_title": "DEV-42",
                "git_status_before": [],
                "git_status_after": ["M distr/gui/web/static/workflows/js/studio.js"] if live_activity else [],
                "latest_backend_handoff": {
                    "git_status_after": ["M distr/gui/web/static/workflows/js/studio.js"],
                } if live_activity else {},
                "provider_preflight": {"provider": "OpenAI", "ready": True} if live_activity else {},
                "coordination_plan": {"assignments": {
                    "1": {"role": "planning", "step_name": "Plan", "status": "completed", "primary_route": {"model": "gpt-plan"}},
                    "2": {"role": "implementation", "step_name": "Build", "status": "running", "primary_route": {"model": "gpt-code"}},
                    "3": {"role": "review", "step_name": "Review", "status": "planned", "primary_route": {"model": "gpt-review"}},
                }},
                "risk_profile": {"level": "high", "signals": ["UI change", "filesystem write"]},
                "budget": {"estimated_cost_usd": 0.0421},
                "drift_metrics": {"human_takeovers": 1},
                "open_url": "/development/threads/17/",
                "related_ticket_url": "/kanban/?ticket_id=42",
                "cancellation_target": {"kind": "workflow", "url": "/api/workflows/44/cancel-run/99"},
            }] if (pending_interaction or live_activity) and not (workflow_cancelled or (complete_after_refresh and active_run_reads > 1)) else [])
        elif path == "/api/workflows/44/runs":
            payload = ([{"id": 99, "workflow_id": 44, "status": "completed"}]
                       if workflow_cancelled or (complete_after_refresh and active_run_reads > 1) else [])
        elif path == "/api/workflows/45/runs":
            payload = []
        elif path == "/api/workflows/45/run":
            payload = {"success": True, "run_id": 100}
        elif path == "/api/workflows/45/generate-steps":
            payload = {"steps": workflow_rows[-1]["steps"]}
        elif path == "/api/workflows/intake/inbox":
            payload = {"items": [
                {"id": 1, "source": "email", "text": "Do not show this"},
                {"id": 2, "source": "jira", "ticket_id": 42, "ticket_title": "DEV-42", "lane": "In progress"},
            ]}
        elif path == "/api/workflows/studio/incoming":
            payload = {"items": [
                {"key": "whatsapp:1", "source": "whatsapp", "source_label": "WhatsApp", "database_id": 1, "source_message_id": "wa-1", "source_thread_id": "menu@g.us", "conversation_label": "Menu Project client", "chat_type": "group", "sender": "Designer", "text": "Please add vegetarian filters to the menu website", "created_at": "2026-08-25T12:00:00Z", "processed": False, "from_me": False, "links": [{"id": 5, "board_id": 70, "board_name": "Decisions delivery", "auto_snapshot": False}, {"id": 8, "board_id": 71, "board_name": "Website refresh", "auto_snapshot": False}], "board_id": 70, "board_name": "Decisions delivery", "link_id": 5, "can_snapshot": True},
                {"key": "whatsapp:2", "source": "whatsapp", "source_label": "WhatsApp", "database_id": 2, "source_message_id": "wa-2", "source_thread_id": "menu@g.us", "conversation_label": "Menu Project client", "chat_type": "group", "sender": "Paul", "text": "I will include the new photographs too", "created_at": "2026-08-25T11:00:00Z", "processed": True, "from_me": True, "links": [{"id": 5, "board_id": 70, "board_name": "Decisions delivery", "auto_snapshot": False}, {"id": 8, "board_id": 71, "board_name": "Website refresh", "auto_snapshot": False}], "board_id": 70, "board_name": "Decisions delivery", "link_id": 5, "can_snapshot": False},
                *([{"key": f"whatsapp:long-{index}", "source": "whatsapp", "source_label": "WhatsApp", "database_id": 100 + index, "source_message_id": f"wa-long-{index}", "source_thread_id": "menu@g.us", "conversation_label": "Menu Project client", "chat_type": "group", "sender": "Designer", "text": "Newest WhatsApp message" if index == 24 else f"Earlier message {index}", "created_at": f"2026-08-25T12:{index:02d}:00Z", "processed": True, "from_me": False, "links": [{"id": 5, "board_id": 70, "board_name": "Decisions delivery", "auto_snapshot": False}]} for index in range(25)] if long_whatsapp_thread else []),
                {"key": "whatsapp:3", "source": "whatsapp", "source_label": "WhatsApp", "source_message_id": "wa-3", "source_thread_id": "27820000000@s.whatsapp.net", "conversation_label": "New client", "chat_type": "private", "sender": "New client", "text": "Can you review my landing page?", "created_at": "2026-08-25T10:00:00Z", "processed": False, "board_id": None, "board_name": "", "link_id": None, "can_snapshot": False},
                {"key": "gmail:gm-1", "source": "gmail", "source_label": "Gmail", "source_message_id": "gm-1", "source_thread_id": "gmail-thread-1", "conversation_label": "Menu launch review", "subject": "Menu launch review", "sender": "Client <client@example.com>", "recipient": "Paul <paul@example.com>", "text": "Please review the launch copy before noon.", "snippet": "Please review the launch copy", "created_at": "2026-08-25T09:00:00Z", "processed": False, "unread": True, "attachments": []},
                {"key": "gmail:gm-2", "source": "gmail", "source_label": "Gmail", "source_message_id": "gm-2", "source_thread_id": "gmail-thread-1", "conversation_label": "Menu launch review", "subject": "Menu launch review", "sender": "Paul <paul@example.com>", "recipient": "Client <client@example.com>", "text": "I will send notes this morning.", "snippet": "I will send notes", "created_at": "2026-08-25T09:15:00Z", "processed": True, "unread": False, "attachments": []},
                {"key": "mailshot:ms-1", "source": "mailshot", "source_label": "Mailshot", "source_message_id": "ms-1", "source_thread_id": "mailshot-thread-1", "conversation_label": "Tensology deployment", "subject": "Tensology deployment", "sender": "Ops <ops@tensology.com>", "recipient": "Paul <paul@tensology.com>", "text": "The deployment report is ready.", "snippet": "The deployment report is ready", "created_at": "2026-08-25T08:30:00Z", "processed": False, "unread": True, "attachments": [{"filename": "report.pdf"}]},
            ], "links": [{"id": 5, "source": "whatsapp", "board_id": 70, "board_name": "Decisions delivery", "label": "Menu Project client", "source_thread_id": "menu@g.us", "auto_snapshot": False}], "counts": {"whatsapp": 3, "gmail": 2, "mailshot": 1}, "channels": {"gmail": {"connected": True, "error": ""}, "mailshot": {"connected": True, "error": ""}}}
        elif path == "/api/tickets/boards/70/whatsapp-links" and request.method == "GET":
            payload = [{"id": 5, "board_id": 70, "phone_jid": "menu@g.us", "phone_number": "", "contact_name": "Menu Project client", "auto_snapshot": False}]
        elif path == "/api/tickets/boards/70/whatsapp-links" and request.method == "POST":
            payload = {"success": True, "id": 6}
        elif path == "/api/tickets/boards/80/whatsapp-links" and request.method == "POST":
            payload = {"success": True, "id": 6}
        elif path == "/api/tickets/boards/70/whatsapp-links/5" and request.method == "DELETE":
            payload = {"success": True}
        elif path == "/api/tickets/boards/70/whatsapp-snapshot-ticket":
            payload = {"success": True, "id": 43, "board_id": 70, "message_count": 1, "chat_id": 17}
        elif path == "/api/workflows/studio/tasks/17/artifacts":
            payload = {"items": [
                {"id": 11, "title": "Unbuilt wireframe", "status": "planned", "content": "", "uri": ""},
                {"id": 12, "title": "Implementation brief", "status": "ready", "content": "Ready brief", "metadata": {}},
            ]}
        elif path == "/api/workflows/studio/tasks/17/plans":
            payload = {"items": [{
                "id": 21,
                "chat_id": 17,
                "workflow_id": 44,
                "revision": 1,
                "mode": autonomy_level if autonomy_level in {"plan", "goal"} else "develop",
                "status": "draft" if autonomy_level == "plan" else "executing",
                "instruction": "Implement and verify DEV-42",
                "snapshot": {"steps": workflow["steps"]},
            }]}
        elif path == "/api/workflows/studio/plans/21" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            payload = {"id": 21, "status": body.get("status"), "revision": 1}
        elif path == "/api/workflows/studio/control-state":
            payload = {
                "channels": {"telegram": {"configured": True, "connected": True}},
                "commands": queued_commands,
                "controls": {"pinned": chat["pinned"], "permission_profile": chat["permission_profile"], "remote_continuation": chat["remote_continuation"]},
                "interactions": ([{
                    "token": "abc123",
                    "run_id": 99,
                    "kind": "approval",
                    "allowed_actions": ["approve", "stop", "feedback"],
                    "telegram_linked": True,
                }] if pending_interaction else []),
            }
        elif path == "/api/workflows/studio/tasks/17/commands" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            command = {"id": 61, "source": body.get("source", "web"), "content": body.get("content"), "status": "queued"}
            queued_commands[:] = [command]
            payload = {"status": "queued", "summary": "Instruction queued safely until a worker can accept it.", "artifacts": [command]}
        elif path == "/api/workflows/studio/tasks/17/commands/dispatch":
            payload = {"status": "queued", "summary": "Instruction is safely queued until a worker can accept it.", "artifacts": queued_commands}
        elif path == "/api/workflows/studio/commands/61" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            if body.get("cancel"):
                queued_commands.clear()
                payload = {"id": 61, "status": "cancelled"}
            else:
                queued_commands[0]["content"] = body.get("content")
                payload = queued_commands[0]
        elif path == "/api/workflows/studio/tasks/17/controls":
            body = json.loads(request.post_data or "{}")
            if "pinned" in body:
                chat["pinned"] = body["pinned"]
            if "permission_profile" in body:
                chat["permission_profile"] = body["permission_profile"]
            if "remote_continuation" in body:
                chat["remote_continuation"] = body["remote_continuation"]
            if "autonomy_level" in body:
                chat["autonomy_level"] = body["autonomy_level"]
            payload = chat
        elif path == "/api/workflows/studio/tasks/17" and request.method == "DELETE":
            deleted_chat_ids.add(17)
            payload = {"success": True, "deleted_chat_id": 17}
        elif path == "/api/workflows/studio/tasks/17" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            chat.update({
                "title": body.get("title", chat["title"]),
                "project_id": body.get("project_id", chat["project_id"]),
                "development_board_key": body.get("board_key", chat.get("development_board_key")),
                "development_board_provider": body.get("board_provider", chat.get("development_board_provider")),
                "development_ticket_id": body.get("ticket_id", chat.get("development_ticket_id")),
                "development_board_ticket_key": body.get("board_ticket_key", chat.get("development_board_ticket_key")),
                "permission_profile": body.get("permission_profile") or chat["permission_profile"],
                "remote_continuation": body.get("remote_continuation", chat["remote_continuation"]),
            })
            payload = {
                "status": "updated",
                "chat_id": 17,
                "title": chat["title"],
                "project_id": chat["project_id"],
                "board_key": chat.get("development_board_key"),
                "board_provider": chat.get("development_board_provider"),
                "ticket_id": chat.get("development_ticket_id"),
                "board_ticket_key": chat.get("development_board_ticket_key"),
                "permission_profile": chat["permission_profile"],
                "remote_continuation": chat["remote_continuation"],
            }
        elif path == "/api/workflows/studio/tasks/17/archive":
            body = json.loads(request.post_data or "{}")
            chat["archived"] = bool(body.get("archived"))
            payload = {"chat_id": 17, "archived": chat["archived"]}
        elif path == "/api/workflows/studio/tasks/17/export":
            payload = {"schema": "decisions-development-thread/v1", "thread": {"id": 17}, "messages": []}
        elif path == "/api/workflows/studio/tasks/17/snapshots":
            payload = {"token": "redacted-token", "artifact_id": 91}
        elif path == "/api/workflows/studio/projects/7/doctor":
            payload = {"ok": False, "summary": {"ready": 8, "missing": 2}, "repair_actions": [{"name": "Codex projection", "reason": "missing"}]}
        elif path == "/api/workflows/studio/tasks/17/skills":
            payload = {"status": "created", "summary": "Saved reusable skill.", "artifacts": [{"path": "/tmp/project/.decisions/skills/ui/SKILL.md"}]}
        elif path == "/api/workflows/studio/artifacts/12" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            payload = {"id": 12, "title": "Implementation brief", "status": body.get("status"), "content": "Ready brief", "metadata": {}}
        elif path == "/api/workflows/studio/interactions/abc123/resolve":
            payload = {"success": True, "run_id": 99, "queued": True}
        elif path == "/api/automations" and request.method == "GET":
            payload = {"automations": automations}
        elif path == "/api/automations/imports" and request.method == "GET":
            imported = "codex" in imported_sources
            payload = {"routing_defaults": {"model_provider": "ollama", "model": "muse-glimmer:30b-mlx", "complexity": "medium", "reasoning_effort": "medium", "execution_environment": "local", "adaptive_model_routing": True, "update_existing": True}, "sources": [
                {"id": "codex", "label": "Codex", "available": True, "found": 1, "new": 0 if imported else 1, "existing": 1 if imported else 0},
                {"id": "cursor", "label": "Cursor", "available": False, "found": 0, "new": 0, "existing": 0},
                {"id": "claude", "label": "Claude", "available": False, "found": 0, "new": 0, "existing": 0},
            ]}
        elif path == "/api/automations/imports" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            routing = body.get("routing") or {}
            already_imported = "codex" in imported_sources
            if not already_imported:
                imported_sources.add("codex")
                automations.append({
                    "id": "auto_imported",
                    "name": "Imported Codex health check",
                    "instruction": "Verify the production health check.",
                    "status": "active",
                    "automation_type": "scheduled_instruction",
                    "source_config": {},
                    "schedule": {"kind": "daily", "time": "09:00", "timezone": "Africa/Johannesburg"},
                    "action_config": {
                        "run_in_new_thread": True,
                        "model_provider": routing.get("model_provider", ""),
                        "model": routing.get("model", ""),
                        "reasoning_effort": routing.get("reasoning_effort", "medium"),
                        "complexity": routing.get("complexity", "medium"),
                        "execution_environment": routing.get("execution_environment", "local"),
                        "adaptive_model_routing": routing.get("adaptive_model_routing", True),
                        "import_source": "Codex",
                        "import_external_id": "codex-health-check",
                    },
                    "next_run_at": "2026-08-30T07:00:00Z",
                    "last_run_at": None,
                })
            elif routing.get("update_existing"):
                automations[-1]["action_config"].update(routing)
            payload = {
                "success": True,
                "imported_count": 0 if already_imported else 1,
                "updated_count": 1 if already_imported and routing.get("update_existing") else 0,
                "skipped_count": 1 if already_imported and not routing.get("update_existing") else 0,
                "imported": [] if already_imported else [automations[-1]],
                "updated": [automations[-1]] if already_imported and routing.get("update_existing") else [],
            }
        elif path == "/api/automations/draft" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            payload = {
                "name": "Weekday visual verification",
                "instruction": body.get("instruction"),
                "schedule": {"kind": "weekly", "time": "08:00", "days": "1,2,3,4,5"},
                "automation_type": "channel_intake" if "whatsapp" in body.get("instruction", "").lower() else "scheduled_instruction",
                "source_config": {"source": "whatsapp", "trigger": "incoming_message"} if "whatsapp" in body.get("instruction", "").lower() else {},
                "link_current_thread": False,
                "drafted_by": "ai",
            }
        elif path == "/api/automations" and request.method == "POST":
            body = json.loads(request.post_data or "{}")
            automation = {
                "id": "auto_1",
                "name": body.get("name"),
                "instruction": body.get("instruction"),
                "status": "active",
                "automation_type": body.get("automation_type", "scheduled_instruction"),
                "source_config": body.get("source_config") or {},
                "schedule": body.get("schedule") or {},
                "action_config": body.get("action_config") or {},
                "thread_chat_id": automation_thread_chat_id,
                "next_run_at": "2026-08-23T09:00:00Z",
                "last_run_at": None,
            }
            automations[:] = [automation]
            payload = {"success": True, "automation": automation}
        elif path == "/api/automations/auto_1" and request.method == "PUT":
            body = json.loads(request.post_data or "{}")
            automations[0].update(body)
            payload = {"success": True, "automation": automations[0]}
        elif path in {"/api/automations/auto_1/runs", "/api/automations/auto_imported/runs"}:
            payload = {"runs": [{"id": "run_1", "status": "completed", "created_at": "2026-08-23T08:00:00Z", "summary": "Checks passed.", "chat_id": 17}]}
        elif path == "/api/automations/auto_1/run":
            payload = {"success": True, "run": {"status": "running", "summary": "Development automation dispatched."}}
        elif path == "/api/automations/auto_1/pause":
            automations[0]["status"] = "paused"
            payload = {"success": True, "automation": automations[0]}
        elif path == "/api/automations/auto_1/resume":
            automations[0]["status"] = "active"
            payload = {"success": True, "automation": automations[0]}
        elif path == "/api/automations/auto_1" and request.method == "DELETE":
            automations.clear()
            payload = {"success": True}
        elif path == "/api/llms/available-providers":
            payload = {"providers": [{"id": "ollama", "name": "Ollama"}, {"id": "openai", "name": "OpenAI"}, {"id": "anthropic", "name": "Anthropic"}]}
        elif path == "/api/models":
            payload = {"models": []}
        elif path == "/api/llms/models":
            payload = {"models": ([{"id": "muse-glimmer:30b-mlx", "name": "Muse Glimmer (30B-MLX)", "local": True}] if "provider=ollama" in request.url else [{
                "id": "gpt-test",
                "name": "GPT Test",
                "reasoning_efforts": ["low", "medium", "high"],
                "service_tiers": ["standard", "priority"],
            }] if "provider=openai" in request.url else [{"id": "claude-test", "name": "Claude Test"}])}
        elif path == "/api/workflows/studio/tasks/17/messages":
            if message_validation_error:
                route.fulfill(
                    status=422,
                    content_type="application/json",
                    body=json.dumps({"detail": [{"loc": ["body", "message"], "msg": "Field required", "type": "missing"}]}),
                )
                return
            if submitted_payloads is not None:
                submitted_payloads.append(json.loads(request.post_data or "{}"))
            payload = {
                "success": True,
                "execution": direct_run or {"id": "direct-new", "status": "initializing", "direct": True},
                "started": True,
            }
        elif path == "/api/workflows/studio/tasks/17/model-route" and request.method == "PATCH":
            body = json.loads(request.post_data or "{}")
            chat.update(body)
            payload = {"id": 17, **body, "workflow_id": 44}
        elif path == "/api/workflows/studio/routing-assessment":
            body = json.loads(request.post_data or "{}")
            payload = {"complexity": "high" if "production" in body.get("instruction", "").lower() else "medium", "operational_state": "neutral", "signals": ["vision"] if body.get("has_images") else [], "route": {"backend": "pi", "model_provider": "openrouter", "model": "leader/sota:free", "fallback_backend": "pi", "fallback_model": "local-coder:30b"}, "reason": "Selected from the free model leaderboard"}
        elif path == "/api/workflows/44/run":
            payload = {"success": True, "run_id": 99}
        elif path == "/api/workflows/44/steps/1/execute":
            payload = {"success": True, "message": "Step execution started."}
        elif path == "/api/workflows/44/steps/1/stop":
            payload = {"success": True}
        elif path == "/api/workflows/44/cancel-run/99":
            workflow_cancelled = complete_after_cancel
            payload = {"success": True, "status": "cancelled"}
        else:
            requests.append(("UNMOCKED", path))
            route.fulfill(status=404, content_type="application/json", body='{"detail":"mock missing"}')
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    page.route("**/api/**", handler)


def test_recents_section_is_hidden_when_no_recent_threads_exist(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, current_board_linked=True)
    page.goto(URL)

    expect(page.locator("#recents-section")).to_be_hidden()
    expect(page.get_by_text("Recents", exact=True)).to_be_hidden()


def test_recents_section_appears_when_an_open_ended_thread_exists(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, unassigned_recent=True)
    page.goto(URL)

    expect(page.locator("#recents-section")).to_be_visible()
    expect(page.get_by_text("Recents", exact=True)).to_be_visible()
    expect(page.get_by_text("Open-ended development", exact=True)).to_be_visible()


def test_board_linked_projectless_thread_is_not_duplicated_in_recents(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, unassigned_recent=True, board_linked_projectless=True)
    page.goto(URL)

    expect(page.locator('[data-chat-id="19"]')).to_have_count(1)
    expect(page.locator('[data-board-group="decisions:70"] [data-chat-id="19"]')).to_be_visible()
    expect(page.locator("#recents-section")).to_be_hidden()


def test_new_thread_wins_over_a_stale_thread_load(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, current_board_linked=True)
    page.add_init_script(
        """
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            const target = String(args[0]?.url || args[0] || '');
            if (target.includes('/api/chats/17')) {
                await new Promise((resolve) => setTimeout(resolve, 300));
            }
            return nativeFetch(...args);
        };
        """
    )
    page.goto(TEST_BASE_URL + "/development/new/")

    page.locator('[data-chat-id="17"]').click()
    page.locator("#new-thread-button").click()
    page.wait_for_timeout(450)

    expect(page.locator("#new-thread-button")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#header-project")).to_have_text("Development")
    expect(page.locator(".task-row-wrap.active")).to_have_count(0)


def test_thread_composer_waits_for_the_url_thread_before_submitting(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, current_board_linked=True)
    page.add_init_script(
        """
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            const target = String(args[0]?.url || args[0] || '');
            if (target.includes('/api/chats/17')) {
                await new Promise((resolve) => setTimeout(resolve, 350));
            }
            return nativeFetch(...args);
        };
        """
    )

    page.goto(URL, wait_until="domcontentloaded")

    expect(page.locator("#task-prompt")).to_be_disabled()
    expect(page.locator("#send-button")).to_be_disabled()
    page.wait_for_timeout(120)
    assert ("POST", "/api/workflows/studio/tasks") not in requests
    assert ("POST", "/api/workflows/studio/tasks/17/messages") not in requests

    expect(page.locator("#task-prompt")).to_be_enabled()
    page.locator("#task-prompt").fill("Continue in this exact thread")
    page.locator("#send-button").click()
    page.wait_for_timeout(250)
    assert requests.count(("POST", "/api/workflows/studio/tasks/17/messages")) == 1
    assert ("POST", "/api/workflows/studio/tasks") not in requests


def test_tray_deep_links_open_board_views_and_automations(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error" and "WebSocket connection" not in message.text
        else None,
    )

    page.goto(TEST_BASE_URL + "/development/boards/decisions/70/")
    expect(page.locator('[data-board-group="decisions:70"] .project-row')).to_have_class(
        re.compile(r"\bactive\b")
    )

    page.goto(TEST_BASE_URL + "/development/boards/decisions/70/kanban/")
    expect(page.locator("#kanban-workspace")).to_be_visible()
    expect(page.locator("#kanban-workspace iframe")).to_have_count(0)
    expect(page.locator("#development-kanban-board")).to_have_text("Decisions delivery")
    expect(page.locator("#kanban-workspace .task-breadcrumb strong")).to_have_text("Kanban")
    expect(page.locator("#kanban-workspace")).not_to_contain_text("Local board")
    expect(page.locator("#development-kanban-refresh")).to_be_visible()
    expect(page.locator("#development-kanban-refresh")).to_have_attribute("aria-label", "Refresh board")
    expect(page.locator("#development-kanban-edit-board")).to_be_visible()
    expect(page.locator("#development-kanban-new-ticket")).to_be_visible()
    page.locator("#development-kanban-new-ticket").click()
    expect(page.locator("#kanban-ticket-dialog")).to_be_visible()
    page.get_by_role("button", name="Close ticket details").click()
    expect(page.locator("#kanban-ticket-dialog")).to_be_hidden()
    expect(page.locator('[data-kanban-lane="701"]')).to_contain_text("In progress")
    expect(page.locator('[data-kanban-ticket="decisions:42"]')).to_contain_text("DEV-42")
    workspace_box = page.locator("#kanban-workspace").bounding_box()
    lane_box = page.locator('[data-kanban-lane="701"]').bounding_box()
    assert workspace_box and workspace_box["height"] > 500
    assert lane_box and lane_box["height"] > 400
    page.screenshot(path="/private/tmp/development-kanban-breadcrumb-desktop.png", full_page=True)

    page.set_viewport_size({"width": 375, "height": 812})
    page.wait_for_timeout(400)
    expect(page.locator("#kanban-workspace .task-breadcrumb strong")).to_be_visible()
    expect(page.locator("#development-kanban-new-ticket")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path="/private/tmp/development-kanban-breadcrumb-compact.png", full_page=True)

    page.goto(TEST_BASE_URL + "/development/boards/decisions/70/settings/")
    expect(page.locator("#board-link-dialog")).to_be_visible()

    page.goto(TEST_BASE_URL + "/development/automations/")
    expect(page.locator("#scheduled-workspace")).to_be_visible()
    assert errors == []


def test_development_navigation_uses_clean_urls_and_restores_history(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.goto(URL)
    expect(page.locator("#task-prompt")).to_be_enabled()

    expect(page.locator("#sidebar-automations-toggle, #sidebar-automation-add")).to_have_count(0)
    page.locator("#sidebar-plan-toggle").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/plan/")
    expect(page.locator("#plan-workspace")).to_be_visible()
    expect(page.locator("#plan-workspace h1")).to_have_text("Plans")
    page.locator("#sidebar-incoming-toggle").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/incoming/")
    expect(page.locator("#incoming-workspace h1")).to_have_count(0)
    page.locator("#sidebar-rules-toggle").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/automations/")
    expect(page.locator("#scheduled-workspace h1")).to_have_text("Scheduled tasks")
    page.locator("#sidebar-workflows-toggle").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/workflows/")
    expect(page.locator("#workflow-workspace h1")).to_have_count(0)
    assert page.locator("#workflow-create-focus").evaluate("node => node.parentElement?.classList.contains('development-toolbar')")
    page.locator("#sidebar-terminals-toggle").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/terminals/")
    expect(page.locator("#terminals-home-workspace")).to_be_visible()
    expect(page.locator("#terminals-home-workspace h1, #terminals-home-workspace h2")).to_have_count(0)
    expect(page.locator("#terminal-project-grid .terminal-project-card")).to_have_count(2)
    expect(page.locator('[data-terminal-project="7"]')).to_contain_text("DecisionsAI")
    expect(page.locator('[data-terminal-project="7"] .terminal-project-metric')).to_have_text("Memory 128 MB")
    expect(page.locator('[data-terminal-project="7"] .terminal-project-titlebar')).to_be_visible()
    expect(page.locator('[data-terminal-project="7"] .terminal-state')).to_have_attribute("aria-label", "Running")
    expect(page.get_by_role("button", name="Stop terminals for DecisionsAI")).to_be_visible()
    expect(page.locator('[data-terminal-project="7"] .terminal-project-command')).to_contain_text("npm run dev")
    expect(page.locator('[data-terminal-project="7"] .terminal-project-open')).to_have_text("Open →")
    expect(page.locator('[data-terminal-project="8"] .terminal-state')).to_have_attribute("aria-label", "Stopped")
    expect(page.locator('[data-terminal-project="8"] .terminal-state')).to_have_class(re.compile(r"\bstopped\b"))
    expect(page.get_by_role("button", name="Start terminals for Empty project")).to_be_visible()
    assert page.locator("#terminal-project-grid").evaluate(
        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
    ) == 3
    assert float(page.locator('[data-terminal-project="7"] .terminal-project-footer').evaluate(
        "node => parseFloat(getComputedStyle(node).fontSize)"
    )) >= 12
    page.screenshot(path="/private/tmp/development-terminal-projects.png", full_page=True)
    page.locator("#sidebar-reports-toggle").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/reports/")
    expect(page.locator("#reports-workspace")).to_be_visible()
    expect(page.locator("#reports-workspace h1, #reports-workspace h2")).to_have_count(0)
    page.screenshot(path="/private/tmp/development-harness-placeholder-desktop.png", full_page=True)

    page.locator('[data-board-select="decisions:70"]').click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/70/kanban/")
    expect(page.locator("#kanban-workspace")).to_be_visible()
    expect(page.locator('[data-board-group="decisions:70"] .project-row')).to_have_class(re.compile(r"\bactive\b"))
    page.locator('[data-chat-id="17"]').first.click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")

    page.go_back()
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/70/kanban/")
    expect(page.locator("#kanban-workspace")).to_be_visible()
    expect(page.locator('[data-board-group="decisions:70"] .project-row')).to_have_class(re.compile(r"\bactive\b"))
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('[data-board-group="decisions:70"] .project-row')).to_have_class(re.compile(r"\bactive\b"))

    page.goto(TEST_BASE_URL + "/workflows/?view=incoming")
    expect(page).to_have_url(TEST_BASE_URL + "/development/incoming/")
    expect(page.locator("#incoming-workspace")).to_be_visible()

    for path, workspace in (
        ("plan", "plan-workspace"),
        ("terminals", "terminals-home-workspace"),
        ("reports", "reports-workspace"),
    ):
        page.goto(TEST_BASE_URL + f"/development/{path}/", wait_until="domcontentloaded")
        expect(page.locator(f"#{workspace}")).to_be_visible()

    page.goto(TEST_BASE_URL + "/development/terminals/7/", wait_until="domcontentloaded")
    expect(page.locator("#terminal-workspace")).to_be_visible()
    expect(page.locator("#sidebar-terminals-toggle")).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator('[data-board-group="decisions:70"] .project-row')).not_to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev")


def test_terminal_route_is_painted_before_slow_shell_hydration(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.add_init_script(
        """
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (...args) => {
            const target = String(args[0]?.url || args[0] || '');
            if (target.endsWith('/api/projects')) await new Promise((resolve) => setTimeout(resolve, 400));
            if (target.includes('/api/workflows/studio/incoming')) await new Promise((resolve) => setTimeout(resolve, 2500));
            return nativeFetch(...args);
        };
        """
    )

    page.goto(TEST_BASE_URL + "/development/boards/decisions/70/terminals/", wait_until="domcontentloaded")
    expect(page.locator("#terminal-workspace")).to_be_visible(timeout=350)
    expect(page.locator("#conversation")).to_be_hidden()
    expect(page.locator("#development-task-header")).to_be_hidden()
    expect(page.locator("#development-composer-layer")).to_be_hidden()
    page.screenshot(path="/private/tmp/development-terminal-route-first-paint.png", full_page=True)

    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev", timeout=1_200)
    expect(page.locator("#terminal-project-name")).to_have_text("DecisionsAI")
    breadcrumb = page.locator(".terminal-project-breadcrumb")
    expect(page.get_by_role("button", name="Back to terminal projects")).to_contain_text("Terminals")
    expect(breadcrumb.locator("#terminal-project-name")).to_have_text("DecisionsAI")
    expect(breadcrumb.locator("#terminal-refresh")).to_have_count(0)
    expect(breadcrumb.locator("#terminal-stop-all")).to_have_count(1)
    expect(breadcrumb.locator("#terminal-start-all")).to_have_count(1)
    expect(page.locator("#terminal-session-list .terminal-session")).to_have_count(1)
    expect(page.locator("#sidebar-terminals-toggle")).to_have_class(re.compile(r"\bactive\b"))


def test_enter_on_last_populated_command_prepares_the_next_terminal_line(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, startup_instructions="npm run dev")
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    first = page.get_by_role("textbox", name="Terminal 1 command")
    first.press("Enter")

    expect(page.locator("#terminal-command-rows input")).to_have_count(2)
    second = page.get_by_role("textbox", name="Terminal 2 command")
    expect(second).to_be_focused()
    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev")
    assert ("PUT", "/api/projects/7") not in requests

    second.fill("npm run worker")
    second.press("Enter")
    expect(page.locator("#terminal-command-rows input")).to_have_count(3)
    expect(page.get_by_role("textbox", name="Terminal 3 command")).to_be_focused()
    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev\nnpm run worker")


def test_command_toolbar_copies_and_pastes_the_full_list_between_projects(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, startup_instructions="npm run dev\nnpm run worker")
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    copy = page.get_by_role("button", name="Copy all terminal commands")
    paste = page.get_by_role("button", name="Paste terminal commands")
    save = page.get_by_role("button", name="Save terminal commands")
    expect(copy.locator("svg")).to_have_count(1)
    expect(paste.locator("svg")).to_have_count(1)
    expect(save.locator("svg")).to_have_count(1)
    assert copy.inner_text().strip() == ""
    assert paste.inner_text().strip() == ""
    assert save.inner_text().strip() == ""
    copy.click()
    expect(page.locator("#studio-toast")).to_contain_text("2 terminal commands copied.")

    page.get_by_role("button", name="Back to terminal projects").click()
    page.locator('[data-terminal-project="8"] [data-terminal-project-open]').first.click()
    expect(page.locator("#terminal-project-name")).to_have_text("Empty project")
    expect(page.locator("#terminal-command-rows input")).to_have_count(1)

    page.get_by_role("button", name="Paste terminal commands").click()
    expect(page.locator("#terminal-command-rows input")).to_have_count(2)
    expect(page.get_by_role("textbox", name="Terminal 1 command")).to_have_value("npm run dev")
    expect(page.get_by_role("textbox", name="Terminal 2 command")).to_have_value("npm run worker")
    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev\nnpm run worker")
    expect(page.locator("#studio-toast")).to_contain_text("2 terminal commands pasted. Save to keep them.")
    assert ("PUT", "/api/projects/8") not in requests
    page.screenshot(path="/private/tmp/development-terminal-command-copy-paste.png", full_page=True)

    page.get_by_role("button", name="Save terminal commands").click()
    assert ("PUT", "/api/projects/8") in requests


def test_running_terminals_use_tabs_instead_of_a_vertical_stack(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=True, second_terminal_session=True)
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    tabs = page.get_by_role("tab")
    expect(tabs).to_have_count(2)
    expect(page.locator(".terminal-session.active")).to_have_count(1)
    expect(page.locator('[data-terminal-session="term-1"]')).to_be_visible()
    expect(page.locator('[data-terminal-session="term-2"]')).to_be_hidden()
    expect(page.locator("#terminal-running-count")).to_have_text("2 terminals")
    expect(page.locator(".terminal-session-bar")).to_have_count(0)
    expect(page.locator(".terminal-session-tab-shell.active .terminal-tab-stop")).to_be_visible()
    expect(page.locator(".terminal-session-tab-shell:not(.active) .terminal-tab-stop")).to_be_hidden()
    expect(page.locator('[data-terminal-session="term-1"] .xterm-rows')).to_contain_text("$ npm run dev")
    expect(page.locator("#terminal-stop-all svg")).to_have_count(1)
    assert page.locator("#terminal-stop-all").inner_text().strip() == ""
    page.screenshot(path="/private/tmp/development-terminal-tabs.png", full_page=True)

    tabs.nth(1).click()
    expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
    expect(page.locator('[data-terminal-session="term-1"]')).to_be_hidden()
    expect(page.locator('[data-terminal-session="term-2"]')).to_be_visible()
    expect(page.locator(".terminal-session-tab-shell.active .terminal-tab-stop")).to_have_attribute("aria-label", "Stop terminal 2")


def test_terminal_viewport_is_compact_and_http_links_open_in_a_new_tab(page: Page):
    requests: list[tuple[str, str]] = []
    terminal_command = "vite --host http://localhost:5173/"
    _install_api(page, requests, terminal_session=True, terminal_command=terminal_command)
    page.add_init_script(
        """
        (() => {
            let terminalConstructor;
            Object.defineProperty(window, 'Terminal', {
                configurable: true,
                get: () => terminalConstructor,
                set: (value) => {
                    terminalConstructor = value;
                    const register = value.prototype.registerLinkProvider;
                    value.prototype.registerLinkProvider = function (provider) {
                        window.__testedTerminalLinkProvider = provider;
                        return register.call(this, provider);
                    };
                },
            });
        })();
        """
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    terminal = page.locator(".terminal-session.active")
    expect(terminal).to_be_visible()
    expect(terminal.locator(".xterm-rows")).to_contain_text("http://localhost:5173/")
    assert terminal.bounding_box()["height"] <= 400
    page.screenshot(path="/private/tmp/development-terminal-compact-links.png", full_page=True)
    link = page.evaluate(
        """() => new Promise((resolve) => {
            window.__testedTerminalLinkProvider.provideLinks(1, (links) => {
                window.__testedTerminalLink = links[0];
                resolve({ text: links[0].text, range: links[0].range });
            });
        })"""
    )
    assert link == {
        "text": "http://localhost:5173/",
        "range": {"start": {"x": 15, "y": 1}, "end": {"x": 36, "y": 1}},
    }
    opened = page.evaluate(
        """() => {
            let call = null;
            const originalOpen = window.open;
            window.open = (...args) => { call = args; return null; };
            window.__testedTerminalLink.activate({}, window.__testedTerminalLink.text);
            window.open = originalOpen;
            return call;
        }"""
    )
    assert opened == ["http://localhost:5173/", "_blank", "noopener,noreferrer"]


def test_terminal_project_cards_show_every_command_and_control_processes(page: Page):
    requests: list[tuple[str, str]] = []
    commands = "\n".join(f"npm run service:{index}" for index in range(1, 13))
    _install_api(
        page,
        requests,
        terminal_session=False,
        startup_instructions=commands,
        projects_include_startup_instructions=False,
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/terminals/")

    card = page.locator('[data-terminal-project="7"]')
    expect(card.locator(".terminal-project-command")).to_have_count(12)
    expect(card.locator(".terminal-project-command").last).to_contain_text("npm run service:12")
    assert card.locator(".terminal-project-screen").evaluate(
        "node => getComputedStyle(node).overflowY === 'auto' && node.scrollHeight > node.clientHeight"
    )
    expect(card.locator(".terminal-project-metric")).to_have_text("12 configured commands")
    expect(card.locator(".terminal-state")).to_have_attribute("aria-label", "Stopped")

    page.get_by_role("button", name="Start terminals for DecisionsAI").click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/terminals/")
    expect(page.get_by_role("button", name="Stop terminals for DecisionsAI")).to_be_visible()
    assert ("POST", "/api/projects/7/startup-terminals/start") in requests

    page.get_by_role("button", name="Stop terminals for DecisionsAI").click()
    expect(page.get_by_role("button", name="Start terminals for DecisionsAI")).to_be_enabled()
    expect(card.locator(".terminal-action-spinner")).to_have_count(0)
    expect(page.locator("#studio-toast")).to_contain_text("Stopped 1 terminal for DecisionsAI.")
    assert ("POST", "/api/projects/7/startup-terminals/stop") in requests


def test_web_terminal_surfaces_follow_external_tray_state_changes(page: Page):
    requests: list[tuple[str, str]] = []
    tray_sessions: list[dict] = []
    _install_api(
        page,
        requests,
        terminal_session=False,
        terminal_status_sessions=tray_sessions,
        terminal_status_available=False,
    )
    page.goto(TEST_BASE_URL + "/development/terminals/")

    expect(page.get_by_role("button", name="Start terminals for DecisionsAI")).to_be_visible()
    tray_sessions.append({
        "process_id": "tray-term-1",
        "pid": 404,
        "command": "npm run dev",
        "cwd": "/tmp/decisions",
        "purpose": "startup",
        "memory_bytes": 33554432,
    })
    expect(page.get_by_role("button", name="Stop terminals for DecisionsAI")).to_be_visible(timeout=3_500)
    expect(page.locator('[data-terminal-project="7"] .terminal-project-metric')).to_have_text("Memory 32 MB")

    page.locator('[data-terminal-project="7"] [data-terminal-project-open]').first.click()
    expect(page.locator("#terminal-running-count")).to_have_text("1 terminal")
    tray_sessions.clear()
    expect(page.locator("#terminal-running-count")).to_have_text("Stopped", timeout=3_500)
    expect(page.locator("#terminal-start-all")).to_be_visible()


def test_terminal_stop_never_uses_an_animated_pending_icon(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=True)
    page.goto(TEST_BASE_URL + "/development/terminals/")

    stop = page.get_by_role("button", name="Stop terminals for DecisionsAI")
    stop.click(no_wait_after=True)

    expect(page.locator('[data-terminal-project="7"] .terminal-action-spinner')).to_have_count(0)


def test_stopping_one_terminal_card_keeps_other_cards_and_stops_shell_sessions(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=True, shell_terminal_session=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/terminals/")

    target = page.locator('[data-terminal-project="7"]')
    other = page.locator('[data-terminal-project="8"]')
    expect(target.locator(".terminal-project-metric")).to_have_text("Memory 192 MB")
    assert float(target.locator(".terminal-project-command code").first.evaluate(
        "node => parseFloat(getComputedStyle(node).fontSize)"
    )) <= 11
    other.evaluate("node => { node.dataset.isolationMarker = 'preserve'; }")

    page.get_by_role("button", name="Stop terminals for DecisionsAI").click()
    expect(page.get_by_role("button", name="Start terminals for DecisionsAI")).to_be_enabled()
    expect(target.locator(".terminal-action-spinner")).to_have_count(0)
    expect(page.locator("#studio-toast")).to_contain_text("Stopped 2 terminals for DecisionsAI.")
    expect(other).to_have_attribute("data-isolation-marker", "preserve")
    assert requests.count(("POST", "/api/projects/kill-terminal")) == 1
    assert ("POST", "/api/projects/7/startup-terminals/stop") in requests


def test_failed_terminal_start_restores_a_stable_play_icon(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=False, terminal_start_success=False)
    page.goto(TEST_BASE_URL + "/development/terminals/")

    card = page.locator('[data-terminal-project="7"]')
    page.get_by_role("button", name="Start terminals for DecisionsAI").click()

    expect(page.get_by_role("button", name="Start terminals for DecisionsAI")).to_be_enabled()
    expect(card.locator(".terminal-action-spinner")).to_have_count(0)
    expect(page.locator("#studio-toast")).to_contain_text("Terminal launch failed")


def test_development_never_restores_or_opens_an_ordinary_chat(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)

    page.goto(TEST_BASE_URL + "/development/", wait_until="domcontentloaded")
    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")
    expect(page.locator("#header-task")).to_have_text("Implement ticket DEV-42")
    expect(page.locator("body")).not_to_contain_text("Ordinary voice chat")

    page.goto(TEST_BASE_URL + "/development/threads/1/", wait_until="domcontentloaded")
    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")
    expect(page.locator("#header-task")).to_have_text("Implement ticket DEV-42")
    assert ("GET", "/api/chats/1") not in requests


def test_sidebar_hover_highlights_the_complete_row_without_boxing_the_add_action(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.goto(TEST_BASE_URL + "/development/automations/", wait_until="domcontentloaded")

    group = page.locator("#sidebar-rules-toggle").locator("..")
    add_button = page.locator("#sidebar-rule-add")
    page.locator("#sidebar-rules-toggle").hover()

    assert group.evaluate("node => getComputedStyle(node).backgroundColor") != "rgba(0, 0, 0, 0)"
    assert page.locator("#sidebar-rules-toggle").evaluate(
        "node => getComputedStyle(node).backgroundColor"
    ) == "rgba(0, 0, 0, 0)"
    assert add_button.evaluate("node => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"

    add_button.hover()
    assert group.evaluate("node => getComputedStyle(node).backgroundColor") != "rgba(0, 0, 0, 0)"
    assert add_button.evaluate("node => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"
    page.screenshot(path="/private/tmp/development-sidebar-full-row-hover.png", full_page=True)


def test_desktop_controls_are_real_and_contextual(page: Page):
    requests: list[tuple[str, str]] = []
    submitted_payloads: list[dict] = []
    _install_api(page, requests, submitted_payloads=submitted_payloads)
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error" and "WebSocket connection" not in message.text
        else None,
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)
    expect(page.locator("#task-prompt")).to_be_enabled()
    expect(page.locator('[data-chat-id="17"]')).to_be_visible()
    expect(page.get_by_text("Ordinary voice chat")).to_have_count(0)
    expect(page.locator('[data-board-select="decisions:80"]')).to_be_visible()
    expect(page.locator('[data-board-select="jira:jira-1"]')).to_contain_text("Jira")
    expect(page.locator('[data-board-select="trello:trello-1"]')).to_contain_text("Trello")
    expect(page.locator('[data-board-select="decisions:70"]')).not_to_contain_text("DecisionsAI")
    expect(page.locator("#board-task-tree .provider-mark, #pinned-board-tree .provider-mark")).to_have_count(0)
    assert page.locator('[data-board-group="jira:jira-1"] .project-row').inner_text().count("Jira") == 1

    page.locator("#new-thread-button").click()
    expect(page.locator("#studio-empty")).to_be_visible()
    expect(page.locator("#new-thread-button")).to_contain_text("New Thread")
    expect(page.locator("#composer-board-label")).to_have_text("No board")
    expect(page.locator("#composer-ticket-picker")).to_be_hidden()
    expect(page.locator("#header-project")).to_have_text("Development")
    timer_box = page.locator("#run-elapsed").bounding_box()
    scope_box = page.locator(".composer-scope-strip").bounding_box()
    toolbar_box = page.locator(".composer-toolbar").bounding_box()
    assert timer_box and scope_box and toolbar_box
    assert timer_box["x"] > page.locator("#composer-board-button").bounding_box()["x"]
    assert scope_box["y"] <= timer_box["y"] < toolbar_box["y"]
    expect(page.locator("#new-task-dialog")).to_have_count(0)
    page.screenshot(path="/private/tmp/development-harness-empty.png", full_page=True)

    page.locator('[data-board-toggle="decisions:70"]').click()
    expect(page.locator('[data-chat-id="17"]')).to_be_hidden()
    page.locator("#sidebar-plan-toggle").click()
    expect(page.locator('[data-chat-id="17"]')).to_be_hidden()
    expect(page.locator('[data-board-toggle="decisions:70"]')).to_have_attribute("aria-expanded", "false")
    page.locator('[data-board-toggle="decisions:70"]').click()
    expect(page.locator('[data-chat-id="17"]')).to_be_visible()

    page.locator('[data-board-select="decisions:80"]').click()
    expect(page.locator("#kanban-workspace")).to_be_visible()
    expect(page.locator("#development-kanban-board")).to_have_text("Empty delivery")
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/80/kanban/")
    expect(page.locator("#new-thread-button")).not_to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator('[data-board-group="decisions:80"] .project-row')).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator('[data-board-select="decisions:80"]')).to_have_attribute("aria-current", "true")

    kanban_button = page.locator('[data-board-kanban="decisions:70"]')
    new_thread_button = page.locator('[data-board-new-chat="decisions:70"]')
    expect(kanban_button).to_be_visible()
    expect(new_thread_button).to_be_visible()
    assert kanban_button.bounding_box()["width"] == new_thread_button.bounding_box()["width"] == 28
    page.screenshot(path="/private/tmp/development-board-actions.png", full_page=True)

    page.locator('[data-board-new-chat="jira:jira-1"]').click()
    expect(page.locator("#studio-empty")).to_be_visible()
    expect(page.locator("#composer-board-label")).to_have_text("Jira engineering")
    expect(page.locator("#composer-board-picker")).to_be_hidden()
    expect(page.locator("#composer-ticket-picker")).to_be_visible()
    expect(page.locator("#new-thread-button")).not_to_have_class(re.compile(r"\bactive\b"))

    page.locator('[data-board-select="decisions:70"]').click(button="right")
    page.get_by_role("menuitem", name="New Ticket").click()
    expect(page.locator("#kanban-ticket-dialog")).to_be_visible()
    expect(page.locator("#kanban-ticket-provider")).to_be_hidden()
    expect(page.locator("#kanban-ticket-tabs")).to_be_hidden()
    expect(page.locator("#kanban-ticket-attachments-tab")).to_be_hidden()
    page.get_by_role("button", name="Close ticket details").click()

    page.locator('[data-board-select="jira:jira-1"]').click(button="right")
    expect(page.locator("#board-context-menu")).to_be_visible()
    assert page.evaluate("document.activeElement.id") == "board-context-menu"
    expect(page.locator("#board-context-menu [role='menuitem']")).to_have_count(13)
    expect(page.locator("#board-context-menu")).not_to_contain_text("Incoming")
    assert page.locator("#board-context-menu").bounding_box()["width"] <= 140
    assert page.locator("#board-context-new-chat").bounding_box()["height"] <= 26
    expect(page.locator("#board-context-pin")).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    page.screenshot(path="/private/tmp/development-harness-board-context.png", full_page=True)
    page.get_by_role("menuitem", name="Pin board").click()
    expect(page.locator("#pinned-board-section")).to_be_visible()
    expect(page.locator("#pinned-board-tree")).to_contain_text("Jira engineering")
    expect(page.locator("#manage-boards-button")).to_have_count(0)

    page.locator('[data-chat-id="17"]').click()
    expect(page.locator('[data-chat-id="17"]')).to_have_attribute("aria-current", "page")
    expect(page.locator('[data-chat-id="17"]').locator("xpath=..")).to_have_class(re.compile(r"\bactive\b"))
    page.locator("#composer-permission-select").select_option("trusted")
    expect(page.locator("#composer-permission-select")).to_have_value("trusted")
    expect(page.locator('[data-thread-manage="17"]')).to_have_count(0)
    page.locator('[data-chat-id="17"]').first.click(button="right")
    page.screenshot(path="/private/tmp/development-thread-context-menu.png", full_page=True)
    page.get_by_role("menuitem", name="Rename thread", exact=True).click()
    expect(page.locator("#thread-rename-dialog")).to_be_visible()
    page.locator("#thread-rename-name").fill("Implement and verify DEV-42")
    page.get_by_role("button", name="Rename", exact=True).click()
    expect(page.locator('[data-chat-id="17"]')).to_contain_text("Implement and verify DEV-42")
    assert ("PATCH", "/api/workflows/studio/tasks/17") in requests

    _open_thread_action(page, "Edit thread")
    expect(page.locator("#thread-dialog")).to_be_visible()
    expect(page.locator("#thread-board-name")).to_have_value("Decisions delivery · Local")
    expect(page.locator("#thread-ticket")).to_have_value("decisions:42")
    page.screenshot(path="/private/tmp/development-thread-edit.png", full_page=True)
    page.locator("#thread-time-value").fill("00:03:00")
    page.get_by_role("button", name="Save changes", exact=True).click()
    expect(page.locator("#thread-dialog")).not_to_be_visible()
    assert ("PATCH", "/api/workflows/studio/tasks/17/time") in requests

    page.locator('[data-chat-id="17"]').click()
    expect(page.locator("#thread-more-button")).to_be_enabled()
    expect(page.locator("#task-inspector")).to_be_hidden()
    _open_details(page)
    expect(page.locator("#task-inspector")).to_be_visible()
    expect(page.locator("#task-inspector")).to_contain_text("Thread review")
    expect(page.locator("#inspector-content")).to_contain_text("Conversation")
    expect(page.locator("#inspector-content")).to_contain_text("Implement DEV-42")
    expect(page.locator("#inspector-content")).to_contain_text("Development execution")
    expect(page.locator("#inspector-content")).to_contain_text("Open workflow")
    expect(page.locator("#inspector-content")).not_to_contain_text("Terminals")
    expect(page.locator('[data-start-workflow="1"]')).to_have_count(0)
    expect(page.locator('[data-open-task-terminals="1"]')).to_have_count(0)
    expect(page.locator('[data-tab="artifacts"]')).to_be_visible()
    expect(page.locator('[data-tab="changes"]')).to_be_hidden()
    expect(page.locator('[data-tab="evidence"]')).to_have_count(0)
    page.locator('[data-tab="artifacts"]').click()
    expect(page.get_by_text("Implementation brief")).to_be_visible()
    expect(page.get_by_text("Unbuilt wireframe")).to_have_count(0)
    page.screenshot(path="/private/tmp/development-harness-thread.png", full_page=True)
    page.locator("#inspector-close").click()
    expect(page.locator("#task-inspector")).to_be_hidden()

    page.locator("#model-button").click()
    expect(page.locator("#model-provider-value")).to_have_text("Auto")
    page.locator('[data-model-pane="provider"]').click()
    expect(page.locator("#model-submenu-list .model-choice")).to_have_count(4)
    expect(page.locator("#model-submenu-list .model-choice").first).to_be_visible()
    submenu_box = page.locator("#model-submenu").bounding_box()
    assert submenu_box and submenu_box["height"] > 80
    page.screenshot(path="/private/tmp/development-harness-models.png", full_page=True)
    page.get_by_role("menuitemradio", name=re.compile("OpenAI")).click()
    expect(page.locator("#model-submenu-search")).to_be_visible()
    page.locator("#model-submenu-search").fill("GPT Test")
    expect(page.locator("#model-submenu-list .model-choice")).to_have_count(1)
    back_box = page.locator("#model-submenu-back").bounding_box()
    header_box = page.locator("#model-submenu-header").bounding_box()
    assert back_box and header_box
    assert abs((back_box["y"] + back_box["height"] / 2) - (header_box["y"] + header_box["height"] / 2)) < 1
    page.screenshot(path="/private/tmp/development-model-search.png", full_page=True)
    desktop_viewport = page.viewport_size
    page.set_viewport_size({"width": 375, "height": 812})
    expect(page.locator("#model-submenu-search")).to_be_visible()
    assert page.locator("#model-submenu-header").evaluate("node => node.scrollWidth <= node.clientWidth")
    page.screenshot(path="/private/tmp/development-model-search-compact.png", full_page=True)
    page.set_viewport_size(desktop_viewport)
    page.get_by_role("menuitemradio", name=re.compile("GPT Test")).click()
    page.locator('[data-model-pane="effort"]').click()
    page.get_by_role("menuitemradio", name="High", exact=True).click()
    expect(page.locator("#model-label")).to_have_text("Pinned · gpt-test · High")
    page.wait_for_timeout(100)
    assert ("PATCH", "/api/workflows/studio/tasks/17/model-route") in requests
    page.keyboard.press("Escape")

    expect(page.locator("#composer-board-picker")).to_be_hidden()
    expect(page.locator("#composer-ticket-picker")).to_be_hidden()
    page.locator("#attach-button").click()
    expect(page.locator("#composer-action-menu")).to_be_visible()
    with page.expect_file_chooser() as chooser_info:
        page.locator('[data-composer-action="files"]').click()
    chooser_info.value.set_files([
        {"name": "reference.png", "mimeType": "image/png", "buffer": b"fake-png"},
        {"name": "notes.md", "mimeType": "text/markdown", "buffer": b"notes"},
    ])
    expect(page.locator("#composer-context")).to_contain_text("reference.png")
    expect(page.locator("#composer-context")).to_contain_text("notes.md")
    page.locator("#attach-button").click()
    expect(page.locator("#composer-skill-list")).not_to_contain_text("Browser QA")
    page.locator("#attach-button").click()
    page.locator("#task-prompt").fill("Run the harness verification")
    page.locator("#send-button").click()
    page.wait_for_timeout(250)
    assert ("POST", "/api/workflows/studio/tasks/17/messages") in requests, requests
    assert submitted_payloads[-1]["message"] == "Run the harness verification"
    assert [item["name"] for item in submitted_payloads[-1]["attachments"]] == ["reference.png", "notes.md"]
    assert submitted_payloads[-1]["attachments"][0]["mime_type"] == "image/png"
    assert submitted_payloads[-1]["use_playwright"] is False
    assert not any(path.endswith("/send-to-agent") for _, path in requests)
    expect(page.locator("#new-thread-button")).to_contain_text("New Thread")
    expect(page.locator("#studio-shell a[href='/tickets/']")).to_have_count(0)
    assert errors == [], (errors, requests)


def test_custom_board_picker_exposes_decisions_jira_and_trello(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    page.locator("#new-thread-button").click()
    page.locator("#composer-board-button").click()
    expect(page.locator("#composer-board-menu")).to_be_visible()
    expect(page.locator("#composer-board-menu .provider-mark")).to_have_count(0)
    assert page.locator("#composer-board-menu").evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(21, 29, 73)"
    expect(page.locator("#composer-board-menu")).not_to_contain_text("DecisionsAI")
    expect(page.locator("#composer-board-menu")).to_contain_text("Jira")
    expect(page.locator("#composer-board-menu")).to_contain_text("Trello")
    page.screenshot(path="/private/tmp/development-harness-board-picker.png", full_page=True)
    page.locator('#composer-board-menu [data-picker-key="jira:jira-1"]').click()
    expect(page.locator("#composer-board-label")).to_have_text("Jira engineering")
    page.locator("#composer-ticket-button").click()
    expect(page.locator("#composer-ticket-menu")).to_contain_text("Jira visual review")
    expect(page.locator("#composer-ticket-menu")).to_contain_text("In review")
    expect(page.locator("#composer-ticket-menu .provider-mark")).to_have_count(0)
    expect(page.locator('#composer-ticket-menu [data-picker-key=""] small')).to_have_count(0)
    expect(page.locator('#composer-ticket-menu [data-picker-key=""]')).not_to_contain_text("Jira engineering")
    page.screenshot(path="/private/tmp/development-harness-ticket-picker.png", full_page=True)


def test_mobile_drawers_and_tap_targets(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(URL)
    page.screenshot(path="/private/tmp/development-harness-mobile.png", full_page=True)
    page.locator("#sidebar-open").click()
    assert "open" in (page.locator("#studio-sidebar").get_attribute("class") or "").split()
    page.wait_for_timeout(220)
    expect(page.locator("#new-thread-button")).to_be_visible()
    page.screenshot(path="/private/tmp/development-harness-mobile-sidebar.png", full_page=True)
    page.locator("#sidebar-close").click(force=True)
    expect(page.locator("#studio-sidebar")).not_to_have_class("studio-sidebar open")

    _open_details(page)
    expect(page.locator("#task-inspector")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#task-inspector")).to_be_hidden()

    sizes = page.locator("button:visible").evaluate_all(
        "els => els.map(el => ({label: el.getAttribute('aria-label') || el.textContent.trim(), w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height}))"
    )
    too_small = [item for item in sizes if item["w"] < 44 or item["h"] < 44]
    assert too_small == [], too_small


def test_mobile_placeholder_navigation_fits_without_horizontal_overflow(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(URL)

    page.wait_for_timeout(300)
    page.locator("#sidebar-open").click()
    expect(page.locator("#studio-sidebar")).to_have_class(re.compile(r"\bopen\b"))
    page.wait_for_timeout(220)
    page.locator("#sidebar-terminals-toggle").click()
    expect(page.locator("#terminals-home-workspace")).to_be_visible()
    expect(page).to_have_url(TEST_BASE_URL + "/development/terminals/")
    page.wait_for_timeout(400)

    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0
    page.screenshot(path="/private/tmp/development-harness-placeholder-mobile.png", full_page=True)


def test_web_and_telegram_share_the_pending_run_control(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, pending_interaction=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    _open_details(page)
    expect(page.get_by_text("Input required in web or Telegram")).to_be_visible()
    page.get_by_role("button", name="Approve", exact=True).click()
    page.wait_for_timeout(150)

    assert ("POST", "/api/workflows/studio/interactions/abc123/resolve") in requests


def test_run_mode_and_contextual_automation_are_functional(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, autonomy_level="plan")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    expect(page.locator("#mode-label")).to_have_text("Plan")
    page.locator("#model-button").click()
    expect(page.locator("#model-dialog")).to_be_visible()
    expect(page.locator("[data-prompt-mode]")).to_have_count(3)
    expect(page.locator('[data-prompt-mode="plan"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('[data-prompt-mode="goal"]')).to_have_attribute("aria-pressed", "false")
    expect(page.locator('[data-prompt-mode="approval"]')).to_have_count(0)
    expect(page.locator("#route-explanation")).to_have_count(0)
    page.locator('[data-prompt-mode="goal"]').click()
    expect(page.locator("#mode-label")).to_have_text("Goal")
    expect(page.locator('[data-prompt-mode="goal"]')).to_have_attribute("aria-pressed", "true")
    assert ("PATCH", "/api/workflows/studio/tasks/17/controls") in requests

    page.locator("#model-button").click()
    page.locator("#prompt-playwright-toggle").click()
    expect(page.locator("#prompt-playwright-toggle")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#composer-skill-badges")).not_to_contain_text("Browser QA")
    expect(page.locator(".prompt-mode-options button").last).to_have_attribute("id", "prompt-playwright-toggle")
    page.screenshot(path="/private/tmp/development-prompt-modes.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    page.wait_for_timeout(50)
    model_box = page.locator("#model-dialog").bounding_box()
    assert model_box is not None
    assert model_box["x"] >= 0
    assert model_box["x"] + model_box["width"] <= 375
    expect(page.locator("[data-prompt-mode]")).to_have_count(3)
    page.screenshot(path="/private/tmp/development-prompt-modes-mobile.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(50)
    page.keyboard.press("Escape")

    page.locator("#mode-button").click()
    expect(page.locator("#mode-dialog")).to_be_visible()
    expect(page.locator('input[name="run-mode"][value="goal"]')).to_be_checked()
    page.locator('input[name="run-mode"][value="goal"]').check()
    page.get_by_role("button", name="Apply mode", exact=True).click()
    expect(page.locator("#mode-label")).to_have_text("Goal")

    _open_details(page)
    expect(page.get_by_role("button", name="Automate this thread", exact=True)).to_have_count(0)
    expect(page.locator('[data-tab="goal"]')).to_be_visible()


def test_scheduled_actions_support_ai_first_unlinked_creation(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, automation_thread_chat_id=17)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    page.locator("#new-thread-button").click()
    expect(page.locator("#new-thread-button")).to_have_class(re.compile(r"active"))
    expect(page.locator("#task-prompt")).to_be_focused()

    page.goto(TEST_BASE_URL + "/development/automations/")
    expect(page.locator("#scheduled-workspace")).to_be_visible()
    page.locator("#scheduled-prompt-input").click()
    page.locator("#scheduled-prompt-input").fill("Run the focused visual tests every weekday at 08:00")
    page.locator("#scheduled-prompt-submit").click()

    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")
    page.goto(TEST_BASE_URL + "/development/automations/")

    expect(page.locator("#scheduled-list")).to_contain_text("Weekday visual verification")
    menu_button = page.get_by_role("button", name="More actions for Weekday visual verification", exact=True)
    expect(menu_button).to_be_visible()
    menu_button.click()
    menu = page.locator("#scheduled-row-menu")
    expect(menu).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Run now", exact=True)).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Edit schedule", exact=True)).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Pause", exact=True)).to_be_visible()
    expect(menu.get_by_role("menuitem", name="Delete", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.locator('[data-scheduled-row="auto_1"]').click()
    expect(page.locator("#scheduled-detail").get_by_role("button", name="Open thread", exact=True)).to_be_visible()
    page.screenshot(path="/private/tmp/development-harness-scheduled-unlinked.png", full_page=True)
    assert ("POST", "/api/automations/draft") in requests
    assert ("POST", "/api/automations") in requests

    page.locator("#scheduled-detail").get_by_role("button", name="Run now", exact=True).click()
    page.wait_for_timeout(100)
    assert ("POST", "/api/automations/auto_1/run") in requests

    provider = page.locator("#scheduled-detail [data-detail-provider]")
    model = page.locator("#scheduled-detail [data-detail-model]")
    reasoning = page.locator("#scheduled-detail [data-detail-reasoning]")
    expect(provider).to_be_visible()
    expect(model).to_be_visible()
    expect(reasoning).to_be_visible()
    provider.select_option("openai")
    expect(page.locator("#scheduled-detail [data-detail-provider]")).to_have_value("openai")
    expect(page.locator("#scheduled-detail [data-detail-model] option")).to_contain_text(["Automatic", "GPT Test"])
    page.locator("#scheduled-detail [data-detail-model]").select_option("gpt-test")
    expect(page.locator("#scheduled-detail [data-detail-model]")).to_have_value("gpt-test")
    page.locator("#scheduled-detail [data-detail-reasoning]").select_option("high")
    expect(page.locator("#scheduled-detail [data-detail-reasoning]")).to_have_value("high")
    page.screenshot(path="/private/tmp/development-scheduled-model-inline.png", full_page=True)

    page.locator("#scheduled-detail").get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator("#automation-dialog")).to_be_visible()
    expect(page.locator("#automation-name")).to_have_value("Weekday visual verification")
    expect(page.locator("#automation-provider")).to_be_visible()
    expect(page.locator("#automation-model")).to_be_visible()
    expect(page.locator("#automation-provider")).to_have_value("openai")
    expect(page.locator("#automation-model")).to_have_value("gpt-test")
    page.locator("#automation-dialog details.automation-model-routing > summary").click()
    page.locator("#automation-fallback-provider").select_option("anthropic")
    page.locator("#automation-fallback-model").select_option("claude-test")
    page.locator("#save-automation-button").click()
    expect(page.locator("#scheduled-detail [data-detail-provider]")).to_have_value("openai")
    expect(page.locator("#scheduled-detail [data-detail-model]")).to_have_value("gpt-test")
    expect(page.locator("#scheduled-detail")).to_contain_text("anthropic / claude-test")
    expect(page.locator("#scheduled-detail")).to_contain_text("high")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(250)
    if page.locator("#sidebar-close").is_visible():
        page.locator("#sidebar-close").evaluate("element => element.click()")
        page.wait_for_timeout(250)
    expect(page.locator("#scheduled-detail [data-detail-provider]")).to_be_visible()
    expect(page.locator("#scheduled-detail [data-detail-model]")).to_be_visible()
    page.screenshot(path="/private/tmp/development-scheduled-model-inline-compact.png", full_page=True)


def test_scheduled_task_three_dot_menu_controls_exact_row(page: Page):
    requests: list[tuple[str, str]] = []
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    _install_api(page, requests)
    page.set_viewport_size({"width": 1280, "height": 820})
    page.goto(TEST_BASE_URL + "/development/automations/")

    expect(page.locator("#scheduled-view-toggle")).to_have_text("Calendar")
    page.locator("#scheduled-view-toggle").click()
    expect(page.locator("#scheduled-calendar")).to_be_visible()
    expect(page.locator("#scheduled-calendar-toolbar")).to_be_visible()
    expect(page.locator("#scheduled-view-toggle")).to_have_text("List")
    page.locator("#scheduled-view-toggle").click()
    expect(page.locator("#scheduled-prompt-input")).to_be_visible()
    page.locator("#scheduled-prompt-input").fill("Run the focused visual tests every weekday at 08:00")
    page.locator("#scheduled-prompt-submit").click()

    menu_button = page.get_by_role("button", name="More actions for Weekday visual verification", exact=True)
    menu_button.click()
    page.screenshot(path="artifacts/automation-three-dot-menu-desktop.png", full_page=True)
    page.locator("#scheduled-row-menu").get_by_role("menuitem", name="Pause", exact=True).click()
    expect(page.locator('[data-scheduled-row="auto_1"]')).to_contain_text("Paused")
    assert ("POST", "/api/automations/auto_1/pause") in requests

    menu_button = page.get_by_role("button", name="More actions for Weekday visual verification", exact=True)
    menu_button.click()
    expect(page.locator("#scheduled-row-menu").get_by_role("menuitem", name="Resume", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(100)
    menu_button = page.get_by_role("button", name="More actions for Weekday visual verification", exact=True)
    menu_button.click()
    page.screenshot(path="artifacts/automation-three-dot-menu-compact.png", full_page=True)
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 1280, "height": 820})
    page.wait_for_timeout(100)
    menu_button = page.get_by_role("button", name="More actions for Weekday visual verification", exact=True)
    menu_button.click()
    page.locator("#scheduled-row-menu").get_by_role("menuitem", name="Edit schedule", exact=True).click()
    expect(page.locator("#automation-dialog")).to_be_visible()
    page.locator("#automation-schedule-kind").select_option("once")
    expect(page.locator("#automation-once-at")).to_be_visible()
    page.locator("#automation-schedule-kind").select_option("monthly")
    expect(page.locator("#automation-monthly-days")).to_be_visible()
    expect(page.locator("#automation-timezone")).to_be_visible()
    page.screenshot(path="artifacts/automation-editor-expanded-desktop.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(100)
    page.screenshot(path="artifacts/automation-editor-expanded-compact.png", full_page=True)
    page.set_viewport_size({"width": 1280, "height": 820})
    page.wait_for_timeout(100)
    page.get_by_role("button", name="Close automation dialog", exact=True).click()

    menu_button.click()
    page.locator("#scheduled-row-menu").get_by_role("menuitem", name="Delete", exact=True).click()
    confirmation = page.locator("#decisions-confirm-modal")
    expect(confirmation).to_be_visible()
    confirmation.get_by_role("button", name="Delete", exact=True).click()
    expect(page.locator('[data-scheduled-row="auto_1"]')).to_have_count(0)
    assert ("DELETE", "/api/automations/auto_1") in requests
    assert console_errors == []


def test_scheduled_import_is_polished_and_duplicate_safe(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/automations/")

    expect(page.get_by_role("heading", name="Scheduled tasks", exact=True)).to_be_visible()
    page.locator("#scheduled-import-open").click()
    expect(page.locator("#automation-import-dialog")).to_be_hidden()
    expect(page.locator("#scheduled-import-menu")).to_be_visible()
    expect(page.locator("[data-automation-import-source]")).to_have_count(1)
    expect(page.locator('[data-automation-import-source="codex"]')).to_contain_text("Codex")
    expect(page.locator('[data-automation-import-source="codex"]')).to_contain_text("1 new · 0 imported")
    expect(page.locator("#scheduled-import-menu")).not_to_contain_text("Cursor")
    expect(page.locator("#scheduled-import-menu")).not_to_contain_text("Claude")
    page.locator('[data-automation-import-source="codex"]').click()

    expect(page.locator("#automation-import-dialog")).to_be_visible()
    expect(page.locator("#scheduled-import-menu")).to_be_hidden()
    expect(page.locator("#automation-import-provider")).to_have_value("ollama")
    expect(page.locator("#automation-import-model")).to_have_value("muse-glimmer:30b-mlx")
    expect(page.locator("#automation-import-complexity")).to_have_value("medium")
    expect(page.locator("#automation-import-route-note")).to_contain_text("Local-first")
    page.screenshot(path="/private/tmp/development-automation-import-modal-final.png", full_page=True)
    page.locator("#automation-import-submit").click()

    expect(page.locator("#studio-toast")).to_have_text("Codex: 1 imported · 0 updated · 0 unchanged")
    expect(page.locator("#automation-import-dialog")).to_be_hidden()
    expect(page.locator("#scheduled-list")).to_contain_text("Imported Codex health check")
    expect(page.locator('[data-scheduled-row="auto_imported"]')).to_have_count(1)
    page.locator('[data-scheduled-select="auto_imported"]').click()
    expect(page.locator("#scheduled-detail [data-detail-provider]")).to_have_value("ollama")
    expect(page.locator("#scheduled-detail [data-detail-model]")).to_have_value("muse-glimmer:30b-mlx")
    expect(page.locator("#scheduled-detail")).to_contain_text("Imported from Codex")

    page.locator("#scheduled-import-open").click()
    expect(page.locator('[data-automation-import-source="codex"]')).to_contain_text("0 new · 1 imported")
    page.locator('[data-automation-import-source="codex"]').click()
    expect(page.locator("#automation-import-dialog")).to_be_visible()
    page.locator("#automation-import-submit").click()
    expect(page.locator("#studio-toast")).to_have_text("Codex: 0 imported · 1 updated · 0 unchanged")
    expect(page.locator('[data-scheduled-row="auto_imported"]')).to_have_count(1)
    assert requests.count(("POST", "/api/automations/imports")) == 2
    page.locator("#scheduled-import-open").click()
    expect(page.locator("#scheduled-import-menu")).to_be_visible()
    page.screenshot(path="/private/tmp/development-scheduled-import-final.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#sidebar-close").click(force=True)
    page.wait_for_timeout(250)
    expect(page.locator("#scheduled-import-menu")).to_be_hidden()
    page.locator("#scheduled-import-open").click()
    expect(page.locator("#scheduled-import-menu")).to_be_visible()
    expect(page.locator('[data-automation-import-source="codex"]')).to_be_visible()
    page.locator('[data-automation-import-source="codex"]').click()
    expect(page.locator("#automation-import-dialog")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path="/private/tmp/development-automation-import-modal-compact.png", full_page=True)


def test_live_agent_tool_changes_and_evidence_are_visible_without_empty_tabs(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, live_activity=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    messages = page.locator("#message-list")
    expect(messages.get_by_text("Implementation started", exact=True)).to_be_visible()
    expect(messages.get_by_text("Terminal", exact=True)).to_be_visible()
    expect(messages.get_by_text("Running the focused test suite", exact=True)).to_be_visible()
    expect(messages.get_by_text("I found the affected route and I am verifying the change.")).to_be_visible()
    expect(page.locator("#message-list")).not_to_contain_text("Development thread")
    expect(page.locator("#message-list")).not_to_contain_text("Working through the task")
    expect(page.locator("#message-list")).not_to_contain_text("Stop turn")
    expect(page.locator("#message-list")).not_to_contain_text("Steer")
    expect(page.locator(".active-turn-history .active-turn-action")).to_have_count(3)
    expect(page.locator(".active-turn-history")).to_contain_text("Inspected project")
    expect(page.locator(".active-turn-history")).to_contain_text("Updated files")
    expect(page.locator(".active-turn-history")).to_contain_text("styles.css")
    expect(page.locator(".active-turn-history")).to_contain_text("+3")
    expect(page.locator(".active-turn-history")).to_contain_text("-1")
    expect(page.locator(".active-turn-history .turn-event-file small")).to_have_css("animation-name", "turn-edit-count-in")
    expect(page.locator(".active-turn-history")).to_contain_text("Browser check finished")
    expect(page.locator(".active-turn-summary")).to_contain_text("Running tests")
    expect(page.locator(".active-command")).to_have_text("python -m pytest -q tests/ui/test_menu.py")
    shimmer = page.locator(".active-turn .loading-message > span")
    expect(shimmer).to_have_css("color", "rgba(0, 0, 0, 0)")
    expect(shimmer).to_have_css("animation-name", "studio-text-shimmer")
    expect(shimmer).to_have_css("animation-duration", "1.9s")
    expect(page.locator(".active-turn .loading-message i")).to_have_count(0)
    expect(page.locator(".active-turn .loading-message")).to_have_class(re.compile(r"\bupdating\b"))
    expect(page.locator(".active-turn")).not_to_contain_text("running")
    expect(page.locator(".active-turn")).not_to_contain_text("Analyzing request")
    expect(page.locator(".active-turn")).not_to_contain_text(re.compile(r"\d+:\d{2}"))
    expect(page.locator(".active-turn")).not_to_contain_text("Working in")
    expect(page.locator(".active-turn")).not_to_contain_text("studio.js")
    expect(page.locator("#run-elapsed")).to_be_visible()
    expect(page.locator("#run-elapsed")).to_have_text(re.compile(r"\d{2}:\d{2}:\d{2}"))
    sidebar_spinner = page.locator('[data-chat-id="17"] .task-run-state')
    expect(sidebar_spinner).to_have_count(1)
    expect(sidebar_spinner).to_have_attribute("role", "status")
    expect(sidebar_spinner).to_have_attribute("aria-label", "Working on this thread")
    expect(sidebar_spinner).to_have_css("animation-name", "thread-run-spin")

    _open_details(page)
    expect(page.locator("#inspector-content")).to_contain_text("Conversation")
    expect(page.locator("#inspector-content")).to_contain_text("Running the focused test suite")
    expect(page.locator("#inspector-content")).to_contain_text("I found the affected route")
    expect(page.locator("#inspector-content").get_by_role("link", name=re.compile("127.0.0.1"))).to_be_visible()
    expect(page.locator("#inspector-content")).not_to_contain_text("Terminals")
    expect(page.get_by_role("button", name="Open terminals", exact=True)).to_have_count(0)
    page.screenshot(path="/private/tmp/development-task-details-operational.png", full_page=True)
    expect(page.locator('[data-tab="changes"]')).to_be_visible()
    expect(page.locator('[data-tab="evidence"]')).to_have_count(0)

    page.locator('[data-tab="changes"]').click()
    expect(page.get_by_text("M distr/gui/web/static/workflows/js/studio.js", exact=True)).to_be_visible()
    page.screenshot(path="/private/tmp/development-harness-live-activity.png", full_page=True)
    page.locator("#inspector-close").click()
    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    page.wait_for_timeout(250)
    expect(page.locator(".active-turn .loading-message > span")).to_be_visible()
    expect(page.locator(".active-command")).to_have_text("python -m pytest -q tests/ui/test_menu.py")
    page.locator("#sidebar-open").click()
    page.wait_for_timeout(250)
    expect(page.locator('[data-chat-id="17"] .task-run-state')).to_be_visible()
    page.screenshot(path="/private/tmp/development-sidebar-spinner-mobile.png", full_page=True)
    page.locator("#sidebar-close").click()
    expect(page.locator(".active-command")).to_have_css("width", re.compile(r"\d+px"))
    dimensions = page.locator("body").evaluate("body => ({body: body.scrollWidth, viewport: document.documentElement.clientWidth})")
    assert dimensions["body"] <= dimensions["viewport"], dimensions
    page.screenshot(path="/private/tmp/development-status-shimmer-compact.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})

    page.locator("#stop-run-button").click()
    expect(page.locator("#decisions-confirm-modal")).to_be_visible()
    page.get_by_role("button", name="Stop run", exact=True).click()
    assert ("POST", "/api/workflows/44/cancel-run/99") in requests

    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_completed_run_removes_stale_working_status(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, live_activity=True, complete_after_refresh=True)
    page.goto(URL)

    expect(page.locator(".active-turn-summary")).to_contain_text("Running tests")
    expect(page.locator(".active-turn")).to_have_count(0, timeout=8_000)


def test_direct_development_agent_is_not_misrepresented_as_a_workflow(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, direct_execution=True, live_activity=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    expect(page.locator(".active-turn-summary")).to_have_text(re.compile(r"Working|Updating|Running"))
    _open_details(page)
    expect(page.locator("#inspector-content")).to_contain_text("Conversation")
    expect(page.locator("#inspector-content")).to_contain_text("Running the focused test suite")
    expect(page.locator("#inspector-content")).not_to_contain_text("Inspect the ticket")
    expect(page.locator("#inspector-content")).not_to_contain_text("Workflow")
    expect(page.locator("#inspector-content")).not_to_contain_text("Terminals")
    assert ("GET", "/api/workflows/44") not in requests, requests

    page.locator("#inspector-close").click()
    page.locator("#task-prompt").fill("Keep the navigation and run the browser checks")
    page.locator("#send-button").click()
    page.wait_for_timeout(100)
    assert ("POST", "/api/workflows/studio/tasks/17/commands") in requests

    page.locator("#stop-run-button").click()
    expect(page.locator("#decisions-confirm-modal")).to_be_visible()
    page.get_by_role("button", name="Stop agent", exact=True).click()
    assert ("POST", "/api/workflows/studio/tasks/17/execution/stop") in requests
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_completed_direct_thread_message_dispatches_without_a_workflow(page: Page):
    requests: list[tuple[str, str]] = []
    submitted_messages: list[dict] = []
    control_state_urls: list[str] = []
    _install_api(page, requests, direct_execution=True, run_status="completed", terminal_session=False)
    page.on(
        "request",
        lambda request: (
            submitted_messages.append(request.post_data_json)
            if urlparse(request.url).path == "/api/workflows/studio/tasks/17/messages"
            else control_state_urls.append(request.url)
            if urlparse(request.url).path == "/api/workflows/studio/control-state"
            else None
        ),
    )
    page.goto(URL)

    expect(page.locator("#task-state b")).to_have_text("Ready")
    _open_details(page)
    expect(page.locator("#inspector-content")).to_contain_text("Conversation")
    expect(page.locator("#inspector-content")).to_contain_text("Implement DEV-42")
    expect(page.locator("#inspector-content")).not_to_contain_text("Implementation complete.")
    expect(page.locator("#inspector-content")).not_to_contain_text("Workflow")
    expect(page.locator("#inspector-content")).not_to_contain_text("Terminals")
    expect(page.get_by_role("button", name="Open terminals", exact=True)).to_have_count(0)
    page.locator("#inspector-close").click()

    instruction = "Add a vegetarian filter to the menu and check that it works in the browser"
    page.locator("#task-prompt").fill(instruction)
    page.locator("#send-button").click()
    page.wait_for_timeout(150)

    assert ("POST", "/api/workflows/studio/routing-assessment") in requests
    assert ("POST", "/api/workflows/studio/tasks/17/messages") in requests
    assert submitted_messages[-1]["message"] == instruction
    assert "create a ticket" not in submitted_messages[-1]["message"].lower()
    expect(page.locator("#model-label")).to_have_text("Auto · leader/sota · Free")
    assert not any(path == "/api/workflows/44/run" for _, path in requests)

    follow_up = "Now tighten the spacing and keep the existing implementation"
    page.locator("#task-prompt").fill(follow_up)
    page.locator("#send-button").click()
    page.wait_for_timeout(150)

    assert requests.count(("POST", "/api/workflows/studio/tasks/17/messages")) == 2
    assert submitted_messages[-1]["message"] == follow_up
    assert not any(path == "/api/workflows/44/run" for _, path in requests)
    assert control_state_urls
    assert all("run_id=" not in url for url in control_state_urls)
    expect(page.locator("body")).not_to_contain_text("Development run started")
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_direct_thread_with_stale_workflow_never_sends_uuid_as_run_id(page: Page):
    requests: list[tuple[str, str]] = []
    control_state_urls: list[str] = []
    _install_api(
        page,
        requests,
        direct_execution=True,
        run_status="completed",
        stale_workflow_on_direct_thread=True,
    )
    page.on(
        "request",
        lambda request: control_state_urls.append(request.url)
        if urlparse(request.url).path == "/api/workflows/studio/control-state"
        else None,
    )

    page.goto(URL)

    expect(page.locator("#task-state b")).to_have_text("Ready")
    page.wait_for_timeout(200)
    assert control_state_urls
    assert all("run_id=" not in url for url in control_state_urls), control_state_urls
    expect(page.locator("#studio-toast")).not_to_contain_text("run_id")
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_structured_submission_error_is_readable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(
        page,
        requests,
        direct_execution=True,
        run_status="completed",
        message_validation_error=True,
    )
    page.goto(URL)

    page.locator("#task-prompt").fill("Test the instruction path")
    page.locator("#send-button").click()

    expect(page.locator("#studio-toast")).to_contain_text("message: Field required")
    expect(page.locator("#studio-toast")).not_to_contain_text("[object Object]")


def test_completed_direct_turn_refreshes_the_assistant_response(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(
        page,
        requests,
        direct_execution=True,
        live_activity=True,
        direct_complete_after_refresh=True,
    )
    page.goto(URL)

    expect(page.locator(".active-turn")).to_be_visible()
    expect(page.locator('[data-chat-id="17"] .task-run-state')).to_be_visible()
    expect(page.locator("#message-list")).to_contain_text("Instruction completed.", timeout=9_000)
    expect(page.locator(".active-turn")).to_have_count(0)
    expect(page.locator('[data-chat-id="17"] .task-run-state')).to_have_count(0)
    assert requests.count(("POST", "/api/workflows/studio/tasks/17/commands/dispatch")) == 0


def test_initializing_direct_turn_immediately_shows_thinking_status(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, direct_execution=True, run_status="initializing")
    page.goto(URL)

    expect(page.locator(".active-turn")).to_have_text("Thinking through the request")
    expect(page.locator(".active-turn .loading-message")).to_have_class(re.compile(r"\bthinking\b"))
    expect(page.locator(".active-turn .loading-message > span")).to_have_css("animation-name", "studio-text-shimmer")
    expect(page.locator(".active-turn .loading-message i")).to_have_count(0)
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_native_stream_renders_partial_response_and_runtime_activity(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, direct_execution=True, live_activity=True, native_stream=True)
    page.goto(URL)

    expect(page.locator(".active-turn-draft strong")).to_have_text("Draft response")
    expect(page.locator(".loading-message.updating")).to_be_visible()
    expect(page.locator(".loading-message.updating")).to_contain_text("Running tests")
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_board_thread_alignment_and_hover_states_are_compact(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, current_board_linked=True)
    page.goto(URL)

    group = page.locator('[data-board-group="decisions:70"]').filter(has=page.locator(".task-row-wrap.active")).first
    board_title = group.locator(".project-select span")
    thread_title = group.locator(".task-title")
    board_box = board_title.bounding_box()
    thread_box = thread_title.bounding_box()
    assert board_box and thread_box
    assert abs(board_box["x"] - thread_box["x"]) <= 1

    board_row_box = group.locator(".project-row").bounding_box()
    active_row = group.locator(".task-row-wrap.active")
    active_row_box = active_row.bounding_box()
    assert board_row_box and active_row_box
    assert abs(board_row_box["x"] - active_row_box["x"]) <= 1
    assert abs(board_row_box["width"] - active_row_box["width"]) <= 1
    thread_marker = active_row.locator(".task-row").evaluate("element => getComputedStyle(element, '::before').content")
    assert thread_marker == "none"
    expect(active_row.locator(".task-run-state")).to_have_count(0)
    expect(active_row.locator(".row-menu-button")).to_have_count(0)
    expect(active_row).to_have_css("box-shadow", "none")
    active_background = active_row.evaluate("element => getComputedStyle(element).backgroundColor")
    active_row.hover()
    expect(active_row).to_have_css("background-color", active_background)
    expect(active_row.locator(".task-row")).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    page.screenshot(path="/private/tmp/development-sidebar-spacing-hover.png", full_page=True)


def test_ticket_picker_is_dynamic_and_thread_scope_locks_after_creation(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, current_board_linked=True)
    page.goto(URL)

    page.locator("#new-thread-button").click()
    expect(page.locator("#composer-board-button")).to_be_enabled()
    expect(page.locator("#composer-ticket-picker")).to_be_hidden()

    page.locator("#composer-board-button").click()
    page.locator('[data-picker-key="decisions:80"]').click()
    expect(page.locator("#composer-board-label")).to_have_text("Empty delivery")
    expect(page.locator("#composer-ticket-picker")).to_be_hidden()
    page.screenshot(path="/private/tmp/development-composer-no-tickets.png", full_page=True)

    page.locator("#composer-board-button").click()
    page.locator('[data-picker-key="decisions:70"]').click()
    expect(page.locator("#composer-ticket-picker")).to_be_visible()
    expect(page.locator("#composer-ticket-button")).to_be_enabled()

    page.locator('[data-board-new-chat="decisions:70"]').click()
    expect(page.locator("#composer-board-picker")).to_be_hidden()
    expect(page.locator("#composer-ticket-picker")).to_be_visible()
    expect(page.locator("#composer-ticket-button")).to_be_enabled()

    page.goto(URL)
    expect(page.locator("#composer-board-picker")).to_be_hidden()
    expect(page.locator("#composer-ticket-picker")).to_be_hidden()
    expect(page.locator("#run-elapsed")).to_be_visible()
    page.screenshot(path="/private/tmp/development-composer-locked-scope.png", full_page=True)

    _open_thread_action(page, "Edit thread")
    expect(page.locator("#thread-board-name")).to_have_attribute("readonly", "")
    expect(page.locator("#thread-ticket")).to_be_enabled()


def test_board_click_falls_back_to_new_thread_when_kanban_is_unavailable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, unavailable_board_id=80)
    page.goto(URL)

    page.locator('[data-board-select="decisions:80"]').click()

    expect(page.locator("#kanban-workspace")).to_be_hidden()
    expect(page.locator("#studio-empty")).to_be_visible()
    expect(page.locator("#composer-board-label")).to_have_text("Empty delivery")
    expect(page.locator("#composer-board-picker")).to_be_hidden()
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/80/")


def test_active_run_keeps_internal_diagnostics_out_and_supports_durable_guidance(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, live_activity=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    _open_details(page)
    expect(page.get_by_text("Agents", exact=True)).to_have_count(0)
    expect(page.get_by_text("Safety and cost", exact=True)).to_have_count(0)
    expect(page.get_by_text("Model route", exact=True)).to_have_count(0)
    page.screenshot(path="/private/tmp/development-harness-control-plane.png", full_page=True)

    page.locator("#inspector-close").click()
    page.locator("#task-prompt").fill("Keep the existing navigation and retest mobile")
    page.locator("#send-button").click()
    _open_details(page)
    expect(page.get_by_text("Queued guidance", exact=False)).to_be_visible()
    expect(page.get_by_text("Keep the existing navigation and retest mobile", exact=True)).to_be_visible()
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(page.locator("#task-prompt")).to_have_value("Keep the existing navigation and retest mobile")
    page.locator("#task-prompt").fill("Keep navigation, retest mobile and tablet")
    page.locator("#send-button").click()
    assert ("POST", "/api/workflows/studio/tasks/17/commands") in requests
    assert ("PATCH", "/api/workflows/studio/commands/61") in requests


def test_compact_actions_workflows_provider_groups_and_board_linking(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    expect(page.locator("#board-task-tree .board-provider-label")).to_have_count(3)
    expect(page.locator("#board-task-tree")).to_contain_text("Jira")
    expect(page.locator("#board-task-tree")).to_contain_text("Trello")
    expect(page.locator('[data-board-group="decisions:70"] .board-link-bead')).to_have_class(re.compile(r"linked"))
    expect(page.locator('[data-board-group="trello:trello-1"] .board-link-bead')).not_to_have_class(re.compile(r"linked"))

    page.locator("#attach-button").click()
    expect(page.locator("#composer-action-menu")).to_be_visible()
    expect(page.locator("#attach-dialog")).not_to_be_visible()
    expect(page.locator('[data-composer-action="files"]')).to_contain_text("Add files")
    expect(page.locator('[data-composer-action="context"]')).to_have_count(0)
    expect(page.locator("#composer-skill-list [data-prompt-skill]")).to_have_count(2)
    page.locator("#composer-skill-search").fill("access")
    expect(page.locator("#composer-skill-list [data-prompt-skill]")).to_have_count(1)
    expect(page.locator("#composer-skill-list")).to_contain_text("Accessibility")
    expect(page.locator("#composer-skill-list [data-prompt-skill] > span")).to_have_css("font-size", "9px")
    expect(page.locator("#composer-skill-list [data-prompt-skill] > span")).to_have_css("text-align", "left")
    page.screenshot(path="/private/tmp/development-composer-skills.png", full_page=True)
    expect(page.locator('[data-composer-action="plan"]')).to_have_count(0)
    expect(page.locator('[data-composer-action="goal"]')).to_have_count(0)
    expect(page.locator('[data-composer-action="workflow"]')).to_have_count(0)

    page.locator("#attach-button").click()
    page.locator("#sidebar-workflows-toggle").click()
    expect(page.locator("#workflow-workspace")).to_be_visible()
    expect(page.locator("#workflow-list")).to_contain_text("Development execution")
    expect(page.locator(".workflow-project-group")).to_contain_text("Reusable workflows")
    expect(page.locator("[data-workflow-card='44']")).to_contain_text("Inspect")
    page.get_by_role("button", name="More actions for Development execution").click()
    expect(page.get_by_role("button", name="Delete workflow")).to_be_visible()
    page.get_by_role("button", name="More actions for Development execution").click()
    page.locator("[data-workflow-card='44'] .workflow-card-open").click()
    expect(page.locator("#workflow-detail")).to_contain_text("1 step")
    page.get_by_role("button", name="List").click()
    expect(page.get_by_role("button", name="Edit step").first).to_be_visible()
    expect(page.get_by_role("button", name="Run step")).to_be_visible()
    expect(page.get_by_role("button", name="Run workflow from this step")).to_be_visible()
    page.get_by_role("button", name="Edit step").click()
    expect(page.locator("#workflow-step-dialog")).to_be_visible()
    page.locator("#workflow-step-name").fill("Independent review")
    page.locator("#workflow-step-dialog .workflow-step-advanced summary").click()
    page.locator("#workflow-step-fresh-agent").check()
    page.locator("#workflow-step-form").get_by_role("button", name="Save step").click()
    expect(page.locator("#workflow-detail")).to_contain_text("Fresh context")
    assert ("PATCH", "/api/workflows/44/steps/1") in requests
    page.get_by_role("button", name="Run step").click()
    assert ("POST", "/api/workflows/44/steps/1/execute") in requests
    page.get_by_role("button", name="Add step").click()
    page.locator("#workflow-step-name").fill("Browser audit")
    page.locator("#workflow-step-instruction").fill("Open the result and verify the acceptance criteria")
    page.locator("#workflow-step-dialog .workflow-step-advanced summary").click()
    page.locator("#workflow-step-validation").select_option("agent")
    page.locator("#workflow-step-routing").select_option("conditional")
    page.locator('[data-workflow-skill-option][value="browser-qa"]').check()
    page.locator('[data-workflow-tool-option][value="playwright"]').check()
    page.locator("#workflow-step-form").get_by_role("button", name="Save step").click()
    expect(page.locator(".workflow-editor-step")).to_have_count(2)
    expect(page.locator("#workflow-detail")).to_contain_text("Browser audit")
    page.locator(".workflow-editor-step").filter(has_text="Browser audit").get_by_role("button", name="Move step up").click()
    assert ("PATCH", "/api/workflows/44/steps/reorder") in requests
    page.locator(".workflow-memory-section > summary").click()
    page.get_by_role("button", name="Load memory").click()
    expect(page.locator(".workflow-memory-content")).to_contain_text("retain corrections")
    page.get_by_role("button", name="Back to workflows").click()
    page.locator("#workflow-prompt-input").fill("Implement a ticket, verify it end to end, and correct failures")
    page.locator("#workflow-prompt-submit").click()
    expect(page.locator("#workflow-detail")).to_contain_text("Ticket delivery loop")
    expect(page.locator("#workflow-detail")).to_contain_text("Implement")
    expect(page.locator("#workflow-detail")).to_contain_text("Verify")
    assert ("POST", "/api/workflows/plan") in requests

    page.locator("#new-thread-button").click()
    page.locator('[data-board-select="decisions:70"]').click(button="right")
    page.get_by_role("menuitem", name="Edit Board").click()
    expect(page.locator("#board-link-dialog")).to_be_visible()
    expect(page.locator("#board-edit-folder")).to_have_value("/tmp/decisions")
    page.locator("#board-edit-terminals").fill("npm run dev\nnpm run worker")
    page.locator("#board-link-workflow").select_option("44")
    page.locator("#board-link-form").get_by_role("button", name="Save board").click()
    expect(page.locator("#board-link-dialog")).not_to_be_visible()
    assert ("PUT", "/api/tickets/boards/70") in requests


def test_workflow_stop_reconciles_without_a_permanent_spinner(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, live_activity=True, complete_after_cancel=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    page.locator("#sidebar-workflows-toggle").click()
    card = page.locator("[data-workflow-card='44']")
    expect(card.get_by_role("button", name="Stop Development execution")).to_be_visible()
    card.get_by_role("button", name="Stop Development execution").click()

    expect(card.get_by_role("button", name="Start Development execution")).to_be_visible()
    expect(card.locator(".workflow-spinner")).to_have_count(0)
    expect(card.locator(".workflow-state")).not_to_have_class(re.compile(r"running"))
    assert ("POST", "/api/workflows/44/cancel-run/99") in requests
    assert requests.count(("GET", "/api/workflows/active-runs")) >= 2


def test_workflows_three_dot_menu_routes_direct_waiting_execution(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, unified_direct_run=True, direct_execution=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/workflows/")

    row = page.locator('[data-active-execution="development:17:direct-91"]')
    expect(row).to_be_visible()
    row.get_by_label("Execution actions").click()
    expect(row.get_by_role("link", name="Open", exact=True)).to_have_attribute("href", "/development/threads/17/")
    expect(row.get_by_role("link", name="Related ticket")).to_be_visible()
    expect(row.get_by_role("link", name="Continue / Respond")).to_be_visible()
    row.get_by_role("button", name="Cancel").click()

    assert ("POST", "/api/workflows/studio/tasks/17/execution/stop") in requests


def test_workflows_three_dot_menu_routes_workflow_cancel(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, live_activity=True, complete_after_cancel=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/workflows/")

    row = page.locator('[data-active-execution="99"]')
    expect(row).to_be_visible()
    row.get_by_label("Execution actions").click()
    expect(row.get_by_role("link", name="Open", exact=True)).to_be_visible()
    row.get_by_role("button", name="Cancel").click()

    assert ("POST", "/api/workflows/44/cancel-run/99") in requests


def test_thread_workflow_picker_links_and_unlinks_an_idle_thread(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, run_status="completed")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    expect(page.locator("#composer-workflow-label")).to_have_text("Development execution")
    page.locator("#composer-workflow-button").click()
    page.locator("#composer-workflow-menu").get_by_role("option", name=re.compile("Direct agent")).click()
    expect(page.locator("#composer-workflow-label")).to_have_text("Direct agent")

    page.locator("#composer-workflow-button").click()
    page.locator("#composer-workflow-menu").get_by_role("option", name=re.compile("Development execution")).click()
    expect(page.locator("#composer-workflow-label")).to_have_text("Development execution")
    expect(page.locator("#inspector-content")).to_contain_text("Open workflow")
    assert requests.count(("PATCH", "/api/workflows/studio/tasks/17/controls")) == 2


def test_workflow_cards_and_editor_are_responsive(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, run_status="completed")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/workflows/")

    expect(page.locator(".workflow-card-grid")).to_be_visible()
    page.screenshot(path="/private/tmp/decisions-workflows-desktop.png", full_page=True)
    page.locator("[data-workflow-card='44'] .workflow-card-open").click()
    expect(page.locator(".workflow-step-section")).to_be_visible()
    expect(page.locator(".workflow-loop-map")).to_be_visible()
    expect(page.locator(".workflow-loop-node")).to_contain_text("A scoped implementation plan")
    expect(page.locator(".workflow-settings-form")).to_be_hidden()
    expect(page.get_by_label("What should work differently?")).to_be_visible()
    page.screenshot(path="/private/tmp/decisions-workflow-editor-loop-desktop.png", full_page=True)

    page.get_by_role("button", name="List").click()
    expect(page.locator(".workflow-step-stack")).to_be_visible()
    expect(page.get_by_role("button", name="Edit step")).to_be_visible()
    page.screenshot(path="/private/tmp/decisions-workflow-editor-list-desktop.png", full_page=True)

    page.get_by_label("What should work differently?").fill("Add a security review after implementation")
    page.get_by_role("button", name="Update workflow").click()
    expect(page.locator("#workflow-detail")).to_contain_text("Security review")
    assert ("POST", "/api/workflows/44/generate-steps") in requests

    page.get_by_role("button", name="Edit step").first.click()
    expect(page.locator("#workflow-step-outcome")).to_have_value("A scoped implementation plan")
    expect(page.locator("#workflow-step-skills")).to_be_visible()
    expect(page.locator(".workflow-step-advanced")).not_to_have_attribute("open", "")
    page.screenshot(path="/private/tmp/decisions-workflow-step-editor-desktop.png", full_page=True)
    page.locator("#workflow-step-outcome").fill("A verified implementation plan")
    page.locator("#workflow-step-form").get_by_role("button", name="Save step").click()
    expect(page.locator("#workflow-detail")).to_contain_text("A verified implementation plan")

    page.get_by_role("button", name="Loop", exact=True).click()

    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    page.locator("#drawer-backdrop").evaluate("node => node.classList.add('hidden')")
    page.wait_for_timeout(250)
    expect(page.locator(".workflow-editor-header")).to_be_visible()
    expect(page.get_by_role("button", name="Edit step").first).to_be_visible()
    assert page.locator("#workflow-workspace").evaluate("node => node.scrollWidth <= node.clientWidth")
    page.screenshot(path="/private/tmp/decisions-workflow-editor-loop-mobile.png", full_page=True)
    page.get_by_role("button", name="Edit step").first.click()
    expect(page.locator("#workflow-step-dialog")).to_be_visible()
    page.screenshot(path="/private/tmp/decisions-workflow-step-editor-mobile.png", full_page=True)


def test_board_actions_and_thread_owned_time_are_operable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, run_status="completed")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    expect(page.locator("#run-elapsed")).to_have_text("00:02:05")
    expect(page.locator("#composer-time-toggle")).to_have_attribute("aria-label", "Start time tracking")
    page.locator("#composer-time-toggle").click()
    expect(page.locator("#composer-time-toggle")).to_have_attribute("aria-label", "Pause time tracking")
    expect(page.locator("#composer-time-toggle span")).to_have_text("■")
    assert ("POST", "/api/workflows/studio/tasks/17/time/play") in requests
    page.locator("#composer-time-toggle").click()
    expect(page.locator("#composer-time-toggle")).to_have_attribute("aria-label", "Start time tracking")
    assert ("POST", "/api/workflows/studio/tasks/17/time/pause") in requests
    page.locator('[data-board-select="decisions:70"]').click(button="right")
    expect(page.locator("#board-context-terminal-toggle")).to_contain_text("Stop Terminals")
    page.get_by_role("menuitem", name="Terminals", exact=True).click()
    expect(page.locator("#terminal-workspace")).to_be_visible()
    expect(page.locator("#sidebar-terminals-toggle")).to_have_class(re.compile(r"\bactive\b"))
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/70/terminals/")
    expect(page.locator("#terminal-session-list")).to_contain_text("npm run dev")
    expect(page.locator("#terminal-session-list .terminal-session")).to_have_count(1)
    expect(page.locator(".terminal-session-tab")).to_have_count(1)
    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev")
    page.screenshot(path="/private/tmp/development-terminal-detail.png", full_page=True)
    page.get_by_role("textbox", name="Terminal 1 command").fill("npm run dev")
    page.locator("#terminal-add-command").click()
    page.get_by_role("textbox", name="Terminal 2 command").fill("npm run worker")
    page.locator("#terminal-save-commands").click()
    expect(page.locator("#terminal-command-lines")).to_have_value("npm run dev\nnpm run worker")
    assert ("PUT", "/api/projects/7") in requests
    expect(page.locator("#terminal-start-all")).to_be_hidden()
    page.locator("#terminal-stop-all").click()
    assert ("POST", "/api/projects/7/startup-terminals/stop") in requests

    page.locator('[data-board-select="decisions:70"]').click(button="right")
    page.get_by_role("menuitem", name="Board View").click()
    expect(page.locator("#kanban-workspace")).to_be_visible()
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/70/kanban/")
    expect(page.locator("#kanban-workspace iframe")).to_have_count(0)
    expect(page.locator('[data-kanban-ticket="decisions:42"]')).to_be_visible()
    page.locator('[data-kanban-ticket="decisions:42"]').drag_to(page.locator('[data-kanban-dropzone="702"]'))
    expect(page.locator('[data-kanban-lane="702"]')).to_contain_text("DEV-42")
    page.wait_for_timeout(250)
    expect(page).to_have_url(TEST_BASE_URL + "/development/boards/decisions/70/kanban/")
    expect(page.locator("#kanban-workspace")).to_be_visible()
    assert ("PUT", "/api/tickets/tickets/42/move") in requests

    page.locator('[data-chat-id="17"]').first.click()
    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")
    page.locator("#thread-more-button").click()
    page.locator("#thread-menu-edit-time").click()
    expect(page.locator("#time-editor-value")).to_have_text("00:02:05")
    page.locator("#time-play-button").click()
    assert ("POST", "/api/workflows/studio/tasks/17/time/play") in requests
    page.locator("#time-reset-button").click()
    expect(page.locator("#time-editor-value")).to_have_text("00:00:00")

    page.locator("#time-dialog").get_by_role("button", name="Done").click()
    page.locator("#thread-more-button").click()
    expect(page.locator("#thread-menu-clear-context")).to_be_enabled()
    page.locator("#thread-menu-clear-context").click()
    page.wait_for_timeout(100)
    assert ("POST", "/api/workflows/studio/tasks/17/clear-context") in requests


def test_board_context_can_start_stopped_project_terminals(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=False)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(URL)

    page.locator('[data-board-select="decisions:70"]').click(button="right")
    toggle = page.locator("#board-context-terminal-toggle")
    expect(toggle).to_contain_text("Start Terminals")
    toggle.click()

    assert ("POST", "/api/projects/7/startup-terminals/start") in requests


def test_started_terminal_workspace_stays_visible_through_shell_polling(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=False)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    expect(page.locator("#terminal-session-list .terminal-session")).to_have_count(0)
    expect(page.locator("#terminal-session-list")).to_contain_text("No terminal processes are running.")
    page.locator("#terminal-start-all").click()
    expect(page.locator("#terminal-session-list .terminal-session")).to_have_count(1)
    expect(page.locator("#terminal-running-count")).to_have_text("1 terminal")
    expect(page.locator("#terminal-start-all")).to_be_hidden()
    expect(page.locator("#terminal-stop-all")).to_be_visible()

    shell = page.locator(".terminal-detail-shell").bounding_box()
    editor = page.locator("#terminal-command-form").bounding_box()
    assert shell and editor
    assert shell["width"] >= 1050
    assert abs(shell["x"] - editor["x"]) < 2
    assert abs(shell["width"] - editor["width"]) <= 2
    expect(page.locator(".terminal-session-tab")).to_have_count(1)

    page.wait_for_timeout(6_500)
    expect(page.locator("#terminal-session-list .terminal-session")).to_have_count(1)
    expect(page.locator("#terminal-running-count")).to_have_text("1 terminal")
    page.screenshot(path="/private/tmp/development-terminal-workspace-persistent.png", full_page=True)

    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    expect(page.get_by_role("textbox", name="Terminal 1 command")).to_be_visible()
    expect(page.locator("#terminal-session-list .terminal-session")).to_be_visible()
    dimensions = page.locator("body").evaluate("body => ({body: body.scrollWidth, viewport: document.documentElement.clientWidth})")
    assert dimensions["body"] <= dimensions["viewport"], dimensions
    page.screenshot(path="/private/tmp/development-terminal-workspace-compact.png", full_page=True)


def test_discovered_processes_are_compact_and_stop_all_reaches_them(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=False, discovered_terminal_session=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    expect(page.locator("#terminal-project-name")).to_have_text("DecisionsAI")
    expect(page.locator("#terminal-session-list .terminal-session")).to_have_count(0)
    expect(page.locator(".terminal-process-row")).to_have_count(1)
    expect(page.locator(".terminal-process-row")).to_contain_text("python3 -m http.server 43117")
    expect(page.locator(".terminal-process-row")).to_contain_text("Detected process")
    page.screenshot(path="/private/tmp/development-terminal-discovered-process.png", full_page=True)
    page.locator("#terminal-stop-all").click()
    expect(page.locator(".terminal-process-row")).to_have_count(0)

    assert ("POST", "/api/projects/kill-terminal") in requests
    assert ("POST", "/api/projects/7/startup-terminals/stop") in requests


def test_managed_terminal_child_is_not_repeated_as_a_detected_process(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(
        page,
        requests,
        terminal_session=True,
        discovered_terminal_session=True,
        discovered_terminal_parent_pid=101,
    )
    page.goto(TEST_BASE_URL + "/development/terminals/7/")

    expect(page.locator(".terminal-session-tab")).to_have_count(1)
    expect(page.locator(".terminal-process-row")).to_have_count(0)
    expect(page.locator("#terminal-running-count")).to_have_text("1 terminal")


@pytest.mark.parametrize(
    ("terminal_session", "label", "action"),
    [(False, "Start Terminals", "start"), (True, "Stop Terminals", "stop")],
)
def test_thread_context_toggles_its_project_terminals(page: Page, terminal_session: bool, label: str, action: str):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, terminal_session=terminal_session)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(URL)

    page.locator('[data-chat-id="17"]').first.click(button="right")
    toggle = page.locator("#thread-context-terminal-toggle")
    expect(toggle).to_contain_text(label)
    toggle.click()

    assert ("POST", f"/api/projects/7/startup-terminals/{action}") in requests


def test_external_kanban_moves_use_cache_and_refresh_only_on_request(page: Page):
    requests: list[tuple[str, str]] = []
    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    page.locator('[data-board-select="jira:jira-1"]').click()
    expect(page.locator("#kanban-workspace")).to_be_visible()
    expect(page.locator('[data-kanban-ticket="jira:DEV-99"] .kanban-source')).to_have_count(0)
    expect(page.locator("#development-kanban-refresh")).to_be_visible()
    expect(page.locator("#development-kanban-refresh")).to_have_attribute("aria-label", "Re-sync Jira board")
    initial_reads = requests.count(("GET", "/api/tickets/external-boards/jira/jira-1"))

    page.locator('[data-kanban-ticket="jira:DEV-99"]').drag_to(page.locator('[data-kanban-dropzone="QA"]'))
    expect(page.locator('[data-kanban-lane="QA"]')).to_contain_text("Jira visual review")
    expect(page.locator("#development-kanban-status")).to_be_hidden()
    assert requests.count(("GET", "/api/tickets/external-boards/jira/jira-1")) == initial_reads
    assert ("PUT", "/api/tickets/external-boards/jira/jira-1/move-ticket") in requests

    page.locator("#development-kanban-refresh").click()
    expect(page.locator("#development-kanban-refresh")).to_be_enabled()
    assert any(url.endswith("/api/tickets/external-boards/jira/jira-1?force_refresh=true") for url in requested_urls)
    page.screenshot(path="/private/tmp/development-kanban-jira-refresh.png", full_page=True)

    page.locator('[data-board-select="trello:trello-1"]').click()
    expect(page.locator("#development-kanban-refresh")).to_be_visible()
    expect(page.locator("#development-kanban-refresh")).to_have_attribute("aria-label", "Re-sync Trello board")

    page.locator('[data-board-select="decisions:70"]').click()
    expect(page.locator("#development-kanban-refresh")).to_be_visible()
    expect(page.locator("#development-kanban-refresh")).to_have_attribute("aria-label", "Refresh board")
    page.screenshot(path="/private/tmp/development-kanban-external-refresh.png", full_page=True)


def test_local_ticket_delete_is_left_aligned_and_requires_confirmation(page: Page):
    requests: list[tuple[str, str]] = []
    requested_urls: list[str] = []
    _install_api(page, requests)
    page.on("request", lambda request: requested_urls.append(request.url))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    page.locator('[data-board-select="decisions:70"]').click()
    page.locator('[data-kanban-ticket="decisions:42"]').click()
    dialog = page.locator("#kanban-ticket-dialog")
    expect(dialog.locator("#kanban-ticket-title")).to_have_css("color", "rgb(248, 250, 252)")
    expect(dialog.locator("#kanban-ticket-description")).to_have_css("color", "rgb(248, 250, 252)")
    expect(dialog.locator("#kanban-ticket-provider")).to_be_hidden()
    expect(dialog.locator("#kanban-ticket-tabs")).to_be_visible()
    expect(dialog.locator("#kanban-ticket-attachments-tab")).to_be_visible()
    expect(dialog.locator("#kanban-ticket-lane")).to_be_hidden()
    expect(dialog.locator("#kanban-ticket-priority")).to_be_hidden()
    assert float(dialog.locator("#kanban-ticket-title").evaluate("node => getComputedStyle(node).fontSize.replace('px', '')")) >= 12
    assert float(dialog.locator("#kanban-ticket-description").evaluate("node => getComputedStyle(node).fontSize.replace('px', '')")) >= 12
    title_box = dialog.locator("#kanban-ticket-title").bounding_box()
    description_box = dialog.locator("#kanban-ticket-description").bounding_box()
    complexity_box = dialog.locator("#kanban-ticket-complexity").bounding_box()
    footer_box = dialog.locator("menu").bounding_box()
    dialog_box = dialog.bounding_box()
    assert title_box is not None and description_box is not None and complexity_box is not None and footer_box is not None and dialog_box is not None
    assert abs(title_box["y"] - complexity_box["y"]) <= 1
    assert footer_box["y"] - (description_box["y"] + description_box["height"]) <= 32
    page.screenshot(path="/private/tmp/development-ticket-dialog-compact.png", full_page=True)
    assert dialog_box["height"] < 650
    page.set_viewport_size({"width": 375, "height": 812})
    page.wait_for_timeout(250)
    compact_box = dialog.bounding_box()
    assert compact_box is not None
    assert compact_box["width"] <= 351
    assert compact_box["height"] <= 788
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path="/private/tmp/development-ticket-dialog-mobile.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    delete_button = dialog.get_by_role("button", name="Delete", exact=True)
    expect(delete_button).to_be_visible()
    delete_box = delete_button.bounding_box()
    save_box = dialog.get_by_role("button", name="Save ticket").bounding_box()
    assert delete_box is not None and save_box is not None
    assert delete_box["x"] < save_box["x"]

    delete_button.click()
    confirmation = page.locator("#decisions-confirm-modal")
    expect(confirmation).to_be_visible()
    expect(confirmation).to_contain_text("Its incoming messages will become available")
    delete_thread = confirmation.get_by_role("checkbox", name="Also delete linked thread")
    expect(delete_thread).to_be_checked()
    page.screenshot(path="/private/tmp/development-ticket-delete-linked-thread.png", full_page=True)
    confirmation.get_by_role("button", name="Delete", exact=True).click()
    expect(dialog).to_be_hidden()
    assert ("DELETE", "/api/tickets/tickets/42") in requests
    assert any(url.endswith("/api/tickets/tickets/42?delete_thread=true") for url in requested_urls)
    expect(page.locator('[data-chat-id="17"]')).to_have_count(0)


def test_ticket_delete_checkbox_can_preserve_the_linked_thread(page: Page):
    requests: list[tuple[str, str]] = []
    requested_urls: list[str] = []
    _install_api(page, requests)
    page.on("request", lambda request: requested_urls.append(request.url))
    page.goto(URL)

    page.locator('[data-board-select="decisions:70"]').click()
    page.locator('[data-kanban-ticket="decisions:42"]').click()
    page.get_by_role("button", name="Delete", exact=True).click()
    confirmation = page.locator("#decisions-confirm-modal")
    delete_thread = confirmation.get_by_role("checkbox", name="Also delete linked thread")
    expect(delete_thread).to_be_checked()
    delete_thread.uncheck()
    confirmation.get_by_role("button", name="Delete", exact=True).click()

    assert any(url.endswith("/api/tickets/tickets/42") for url in requested_urls)
    assert not any("delete_thread=true" in url for url in requested_urls)
    expect(page.locator('[data-chat-id="17"]')).to_be_visible()


def test_ticket_material_creates_a_reviewable_thread_draft_without_submitting(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    page.locator('[data-board-select="jira:jira-1"]').click()
    page.locator('[data-kanban-ticket="jira:DEV-99"]').click()
    dialog = page.locator("#kanban-ticket-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).not_to_contain_text("Work on ticket")
    expect(dialog.get_by_role("button", name="Create thread")).to_be_visible()
    expect(page.locator("#kanban-ticket-tabs")).to_be_visible()
    expect(dialog.get_by_role("tab", name="Details")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#kanban-ticket-details-panel")).to_be_visible()
    expect(page.locator("#kanban-ticket-attachments-panel")).to_be_hidden()
    expect(page.locator("#kanban-ticket-attachments")).to_contain_text("composer.png")
    expect(page.locator("#kanban-ticket-attachments")).to_contain_text("requirements.pdf")
    expect(page.locator("#kanban-ticket-attachments img")).to_have_count(1)
    expect(page.locator("#kanban-ticket-comment-count")).to_have_text("1 comment")

    dialog.get_by_role("tab", name=re.compile("Links & files")).click()
    expect(dialog.get_by_role("tab", name=re.compile("Links & files"))).to_have_attribute("aria-selected", "true")
    expect(page.locator("#kanban-ticket-details-panel")).to_be_hidden()
    expect(page.locator("#kanban-ticket-attachments-panel")).to_be_visible()
    page.locator("#kanban-ticket-attachments").evaluate(
        "node => { const row = node.firstElementChild; for (let i = 0; i < 30; i += 1) node.appendChild(row.cloneNode(true)); }"
    )
    attachment_panel = page.locator("#kanban-ticket-attachments-panel")
    assert attachment_panel.evaluate("node => node.scrollHeight > node.clientHeight")
    attachment_panel.evaluate("node => { node.scrollTop = node.scrollHeight; }")
    assert attachment_panel.evaluate("node => node.scrollTop > 0")
    expect(dialog.get_by_role("button", name="Create thread")).to_be_visible()

    dialog.get_by_role("button", name="Create thread").click()
    expect(dialog).to_be_hidden()
    expect(page.locator("#task-prompt")).to_have_value("Jira visual review\n\nReview the custom composer")
    expect(page.locator("#composer-context")).to_contain_text("composer.png")
    expect(page.locator("#composer-context")).to_contain_text("requirements.pdf")
    expect(page.locator("#composer-context")).to_contain_text("ticket-comments.txt")
    expect(page.locator("#composer-ticket-label")).to_contain_text("Jira visual review")
    assert ("POST", "/api/tickets/thread-draft") in requests
    assert ("POST", "/api/workflows/studio/tasks") not in requests
    page.screenshot(path="/private/tmp/development-ticket-thread-draft.png", full_page=True)


def test_incoming_channels_automation_rules_and_step_models_are_operable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, long_whatsapp_thread=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    incoming_group = page.locator("#sidebar-incoming-toggle").locator("..")
    assert abs(incoming_group.bounding_box()["width"] - page.locator(".sidebar-scroll").bounding_box()["width"] + 20) < 2
    page.locator("#sidebar-incoming-toggle").click()
    expect(page.locator("#incoming-workspace")).to_be_visible()
    expect(page.locator("#incoming-link-whatsapp")).to_have_count(0)
    expect(page.locator("#incoming-whatsapp-dialog")).to_have_count(0)
    expect(page.locator("#incoming-callout")).to_have_count(0)
    expect(page.locator("#incoming-search-input")).to_have_count(0)
    page.screenshot(path="/private/tmp/development-harness-incoming.png", full_page=True)
    expect(page.locator("#incoming-list")).to_contain_text("Menu Project client")
    expect(page.locator("#incoming-list")).to_contain_text("Linked work")
    expect(page.locator("#incoming-list")).to_contain_text("Other conversations")
    expect(page.locator("#incoming-folder-whatsapp")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#incoming-folder-gmail")).to_have_count(1)
    expect(page.locator("#incoming-folder-mailshot")).to_be_visible()
    expect(page.get_by_role("tab", name=re.compile("Telegram", re.I))).to_have_count(0)
    expect(page.locator(".incoming-conversation-row")).to_have_count(2)
    expect(page.get_by_role("heading", name="WhatsApp", exact=True)).to_have_count(1)
    expect(page.locator(".incoming-source")).to_have_count(0)
    expect(page.locator('[data-incoming-conversation="whatsapp:menu@g.us"]')).to_contain_text("1 new")
    expect(page.locator("#incoming-whatsapp-reader")).to_contain_text("I will include the new photographs too")
    expect(page.locator("#incoming-whatsapp-reader")).to_contain_text("Website refresh")
    expect(page.locator("#incoming-whatsapp-reader")).to_contain_text("The first snapshot includes the last 2 days")
    whatsapp_messages = page.locator(".incoming-whatsapp-messages")
    page.wait_for_timeout(100)
    assert whatsapp_messages.evaluate("node => node.scrollHeight > node.clientHeight")
    assert whatsapp_messages.evaluate("node => Math.abs(node.scrollHeight - node.clientHeight - node.scrollTop) <= 2")
    expect(whatsapp_messages.locator(".incoming-message-bubble p").last).to_have_text("Newest WhatsApp message")
    expect(whatsapp_messages.locator(".incoming-message-bubble p").last).to_have_css("font-size", "12px")
    expect(page.locator(".incoming-row-preview").first).to_have_css("font-size", "10px")
    page.screenshot(path="/private/tmp/development-incoming-whatsapp-latest.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    page.locator("#incoming-folder-gmail").click()
    page.locator("#incoming-folder-whatsapp").click()
    page.wait_for_timeout(250)
    assert page.locator("body").evaluate("node => node.scrollWidth <= document.documentElement.clientWidth")
    expect(page.locator(".incoming-whatsapp-messages .incoming-message-bubble p").last).to_be_visible()
    page.locator("#incoming-whatsapp-reader").scroll_into_view_if_needed()
    page.screenshot(path="/private/tmp/development-incoming-whatsapp-latest-compact.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.locator("#incoming-folder-gmail").click()
    expect(page.locator("#incoming-panel-gmail")).to_be_visible()
    expect(page.locator("#incoming-gmail-list .incoming-gmail-thread")).to_have_count(1)
    expect(page.locator("#incoming-gmail-reader")).to_contain_text("Menu launch review")
    expect(page.locator("#incoming-gmail-reader")).to_contain_text("Please review the launch copy before noon.")
    expect(page.locator("#incoming-gmail-reader")).to_contain_text("I will send notes this morning.")
    page.locator("#incoming-folder-mailshot").click()
    expect(page.locator("#incoming-panel-mailshot")).to_be_visible()
    expect(page.locator("#incoming-mailshot-list")).to_contain_text("Tensology deployment")
    expect(page.locator("#incoming-mailshot-reader")).to_contain_text("The deployment report is ready.")
    expect(page.locator("#incoming-mailshot-reader")).to_contain_text("report.pdf")
    page.locator("#incoming-folder-whatsapp").click()
    unlinked = page.locator('[data-incoming-conversation="whatsapp:27820000000@s.whatsapp.net"]')
    unlinked.click(button="right")
    expect(page.locator("#incoming-link-dialog")).to_be_visible()
    page.locator("#incoming-link-board").select_option("80")
    page.locator("#incoming-link-dialog").get_by_role("button", name="Link board").click()
    assert ("POST", "/api/tickets/boards/80/whatsapp-links") in requests
    page.get_by_role("button", name=re.compile("Snapshot to Decisions delivery")).click()
    assert ("POST", "/api/tickets/boards/70/whatsapp-snapshot-ticket") in requests
    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")

    page.goto(TEST_BASE_URL + "/development/automation-rules/")
    expect(page.locator("#scheduled-workspace")).to_be_visible()
    page.locator("#scheduled-prompt-input").fill("When a WhatsApp message arrives create a ticket for the linked board")
    page.locator("#scheduled-prompt-submit").click()
    expect(page.locator("#scheduled-list")).to_contain_text("Weekday visual verification")
    assert ("POST", "/api/automations") in requests

    page.locator("#sidebar-workflows-toggle").click()
    page.locator("[data-workflow-card='44'] .workflow-card-open").click()
    page.get_by_role("button", name="Edit step").click()
    page.locator("#workflow-step-dialog .workflow-step-advanced summary").click()
    page.locator("#workflow-step-provider").select_option("openai")
    page.locator("#workflow-step-model").select_option("gpt-test")
    page.locator("#workflow-step-form").get_by_role("button", name="Save step").click()
    assert ("PATCH", "/api/workflows/44/steps/1") in requests


def test_incoming_snapshot_reports_progress_before_the_server_finishes(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.goto(TEST_BASE_URL + "/development/incoming/")
    page.evaluate("""
        () => {
            const realFetch = window.fetch.bind(window);
            window.fetch = (input, options) => {
                const url = String(input || '');
                if (url.includes('/api/tickets/boards/70/whatsapp-snapshot-ticket')) {
                    return new Promise((resolve) => {
                        window.__finishIncomingSnapshot = () => resolve(new Response(JSON.stringify({
                            success: true,
                            id: 43,
                            board_id: 70,
                            message_count: 2,
                            chat_id: 17,
                            prepared_prompt: 'Please implement the menu updates from this WhatsApp snapshot.',
                            attachments: [{
                                name: 'transcript.md',
                                path: '/tmp/transcript.md',
                                mime_type: 'text/markdown',
                                size: 512
                            }]
                        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
                    });
                }
                return realFetch(input, options);
            };
        }
    """)

    button = page.locator('[data-incoming-snapshot="5"]')
    expect(button).to_have_text("Snapshot to Decisions delivery")
    button.click()

    expect(button).to_be_disabled()
    expect(button).to_have_attribute("aria-busy", "true")
    expect(button).to_have_text("Creating snapshot...")
    expect(page.locator("#incoming-whatsapp-reader")).to_have_class(re.compile("snapshot-creating"))

    page.evaluate("window.__finishIncomingSnapshot()")
    expect(page).to_have_url(TEST_BASE_URL + "/development/threads/17/")
    expect(page.locator("#task-prompt")).to_have_value("Please implement the menu updates from this WhatsApp snapshot.")
    expect(page.locator("#composer-context")).to_contain_text("transcript.md")


def test_incoming_google_reconnect_starts_oauth_and_returns_to_incoming(page: Page):
    requests: list[tuple[str, str]] = []
    oauth_requests: list[str] = []
    _install_api(page, requests)

    def route_incoming(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "items": [],
                "links": [],
                "counts": {},
                "channels": {
                    "gmail": {"connected": False, "error": "Reconnect Gmail to load inbox threads."},
                    "mailshot": {"connected": False, "error": ""},
                },
            }),
        )

    def route_oauth(route):
        oauth_requests.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"url": TEST_BASE_URL + "/oauth-test-complete"}),
        )

    page.route("**/api/workflows/studio/incoming?*", route_incoming)
    page.route("**/api/advanced/google/oauth-url?*", route_oauth)
    page.goto(TEST_BASE_URL + "/development/incoming/")
    page.locator("#incoming-folder-gmail").click()

    expect(page.get_by_role("button", name="Connect Google", exact=True)).to_be_visible()
    page.get_by_role("button", name="Connect Google", exact=True).click()
    page.wait_for_url(TEST_BASE_URL + "/oauth-test-complete")

    assert len(oauth_requests) == 1
    assert parse_qs(urlparse(oauth_requests[0]).query)["return_to"] == ["/development/incoming/"]


def test_running_thread_timer_uses_server_snapshot_without_timezone_double_count(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, run_status="running", running_timer=True)
    page.goto(URL)

    expect(page.locator("#run-elapsed")).to_have_text(re.compile(r"^00:00:[0-5]\d$"))
    expect(page.locator("#run-elapsed")).not_to_contain_text("02:")


def test_playwright_is_a_per_prompt_browser_flag_not_an_injected_skill(page: Page):
    requests: list[tuple[str, str]] = []
    submitted_payloads: list[dict] = []
    _install_api(page, requests, submitted_payloads=submitted_payloads)
    page.goto(URL)

    page.locator("#model-button").click()
    expect(page.locator(".prompt-mode-options button").last).to_have_attribute("id", "prompt-playwright-toggle")
    page.locator("#prompt-playwright-toggle").click()
    page.keyboard.press("Escape")
    page.locator("#task-prompt").fill("Check the local preview")
    page.locator("#send-button").click()
    page.wait_for_timeout(200)

    assert submitted_payloads[-1]["message"] == "Check the local preview"
    assert submitted_payloads[-1]["use_playwright"] is True
    assert "browser-qa" not in submitted_payloads[-1]["message"]
    assert "Focused context" not in submitted_payloads[-1]["message"]
    expect(page.locator("#prompt-playwright-toggle")).to_have_attribute("aria-pressed", "false")


def test_prompt_skills_are_structured_icons_and_do_not_bloat_the_user_message(page: Page):
    requests: list[tuple[str, str]] = []
    submitted_payloads: list[dict] = []
    _install_api(page, requests, submitted_payloads=submitted_payloads, message_skills=["accessibility"])
    page.goto(URL)

    existing_icon = page.locator(".studio-message.user .message-skill-icon")
    expect(existing_icon).to_have_count(1)
    expect(existing_icon).to_have_attribute("title", "Accessibility")

    page.locator("#attach-button").click()
    page.locator('[data-prompt-skill="accessibility"]').click()
    page.locator('[data-prompt-skill="humanizer"]').click()
    expect(page.locator("#composer-skill-badges .composer-skill-badge")).to_have_count(2)
    expect(page.locator("#composer-skill-badges .composer-skill-badge > span")).to_have_count(0)
    page.locator("#attach-button").click()
    page.screenshot(path="/private/tmp/development-structured-skill-icons.png", full_page=True)
    page.locator("#task-prompt").fill("Use these skills and tell me what to do")
    page.locator("#send-button").click()
    page.wait_for_timeout(200)

    assert submitted_payloads[-1]["message"] == "Use these skills and tell me what to do"
    assert submitted_payloads[-1]["skill_ids"] == ["accessibility", "humanizer"]
    assert "Focused context" not in submitted_payloads[-1]["message"]
    assert "read_harness_skill" not in submitted_payloads[-1]["message"]


def test_legacy_generated_skill_instructions_render_as_an_icon(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, legacy_skill_message=True)
    page.goto(URL)

    user_message = page.locator(".studio-message.user .user-message")
    expect(user_message).to_contain_text("Use these skills and tell me what to do")
    expect(user_message).not_to_contain_text("Focused context")
    expect(user_message).not_to_contain_text("read_harness_skill")
    expect(user_message.locator(".message-skill-icon")).to_have_count(1)


def test_auto_routing_is_visible_and_manual_image_incompatibility_blocks_send(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1200, "height": 800})
    page.goto(URL)

    page.locator("#new-thread-button").click()
    page.locator("#task-prompt").fill("Repair the production workflow migration")
    page.locator("#send-button").click()
    expect(page.locator("#model-label")).to_have_text("Auto · leader/sota · Free")
    assert ("POST", "/api/workflows/studio/routing-assessment") in requests

    page.goto(URL)
    page.locator("#model-button").click()
    page.locator('[data-model-pane="provider"]').click()
    page.get_by_role("menuitemradio", name=re.compile("Anthropic")).click()
    page.get_by_role("menuitemradio", name=re.compile("Claude Test")).click()
    expect(page.locator("#model-effort-row")).to_be_hidden()
    expect(page.locator("#model-speed-row")).to_be_hidden()
    page.locator("#context-file-input").set_input_files({"name": "reference.png", "mimeType": "image/png", "buffer": b"image"})
    page.locator("#task-prompt").fill("Use this reference")
    before = requests.count(("POST", "/api/workflows/studio/tasks/17/messages"))
    page.locator("#send-button").click()
    expect(page.locator("#studio-toast")).to_contain_text("not image-capable")
    assert requests.count(("POST", "/api/workflows/studio/tasks/17/messages")) == before


def test_thread_context_lifecycle_and_task_details_stay_operable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    _open_thread_action(page, "Pin thread")
    expect(page.locator('#pinned-board-section [data-chat-id="17"]')).to_be_visible()

    _open_details(page)
    expect(page.get_by_role("button", name="Check board harness", exact=True)).to_have_count(0)
    expect(page.get_by_text("Model route", exact=True)).to_have_count(0)
    expect(page.get_by_text("Thread controls", exact=True)).to_have_count(0)
    page.locator("#inspector-close").click()

    _open_thread_action(page, "Archive thread")
    expect(page.locator(".archived-threads summary")).to_contain_text("Archived")
    page.locator(".archived-threads summary").click()
    _open_thread_action(page, "Restore thread")
    expect(page.locator('[data-chat-id="17"]')).to_be_visible()

    assert ("PATCH", "/api/workflows/studio/tasks/17/controls") in requests
    assert ("GET", "/api/workflows/studio/projects/7/doctor") not in requests
    assert requests.count(("POST", "/api/workflows/studio/tasks/17/archive")) == 2
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_artifact_review_is_operable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    _open_details(page)
    expect(page.locator('[data-tab="plan"]')).to_be_hidden()
    expect(page.locator('[data-tab="goal"]')).to_be_hidden()
    page.locator('[data-tab="artifacts"]').click()
    page.get_by_text("Implementation brief", exact=True).click()
    expect(page.locator("#artifact-dialog")).to_be_visible()
    expect(page.locator("#artifact-viewer-content")).to_have_text("Ready brief")
    page.get_by_role("button", name="Approve", exact=True).click()
    page.wait_for_timeout(100)

    assert ("PATCH", "/api/workflows/studio/artifacts/12") in requests


def test_plan_goal_views_and_thread_context_menu_are_operable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, autonomy_level="plan")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    _open_details(page)
    page.locator('[data-tab="plan"]').click()
    expect(page.get_by_text("Version 1", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Approve version 1")).to_be_visible()
    page.screenshot(path="/private/tmp/development-harness-plan.png", full_page=True)
    page.get_by_role("button", name="Approve version 1").click()
    expect(page.locator("#studio-toast")).to_contain_text("Plan approved")
    assert ("PATCH", "/api/workflows/studio/plans/21") in requests

    expect(page.locator('[data-tab="goal"]')).to_be_hidden()

    page.locator('[data-chat-id="17"]').click(button="right")
    expect(page.locator("#thread-context-menu")).to_be_visible()
    page.get_by_role("menuitem", name="Pin thread").click()
    assert ("PATCH", "/api/workflows/studio/tasks/17/controls") in requests
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_goal_tab_only_appears_for_goal_mode(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, autonomy_level="goal")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    _open_details(page)
    expect(page.locator('[data-tab="plan"]')).to_be_hidden()
    expect(page.locator('[data-tab="goal"]')).to_be_visible()
    page.locator('[data-tab="goal"]').click()
    expect(page.locator("#inspector-content")).to_contain_text("Persistent outcome")
    expect(page.locator("#inspector-content")).to_contain_text("Implement ticket DEV-42")


def test_thread_destructive_controls_are_guarded_and_operable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    expect(page.locator('[data-project-manage]')).to_have_count(0)
    expect(page.locator('[data-board-new-chat="decisions:70"]')).to_be_visible()

    _open_thread_action(page, "Delete thread")
    expect(page.locator("#decisions-confirm-modal")).to_be_visible()
    page.get_by_role("button", name="Delete", exact=True).click()
    expect(page.locator("#studio-empty")).to_be_visible()
    assert ("DELETE", "/api/workflows/studio/tasks/17") in requests
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_assistant_results_render_as_readable_markdown(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, assistant_markdown=True)
    page.goto(URL)

    expect(page.locator(".message-markdown h3")).to_have_text("Result")
    expect(page.locator(".message-markdown strong")).to_have_text("Implemented")
    expect(page.locator(".message-markdown code.file-ref").first).to_have_text("index.html")
    expect(page.locator(".message-markdown .md-list-item").first).to_contain_text("Updated")
    expect(page.locator(".message-markdown table")).to_be_visible()
    expect(page.locator(".message-markdown thead th")).to_have_count(2)
    expect(page.locator(".message-markdown tbody tr")).to_have_count(2)
    expect(page.locator(".message-markdown .diff-remove")).to_contain_text("ready = false")
    expect(page.locator(".message-markdown .diff-add")).to_contain_text("ready = true")
    expect(page.locator('.message-markdown a[href="http://localhost:5174/"]')).to_have_text("http://localhost:5174/")
    expect(page.locator('.message-markdown a[href="http://localhost:5174/"]')).to_have_attribute("target", "_blank")


def test_completed_turns_keep_codex_style_collapsible_activity(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, assistant_markdown=True, completed_activity=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    timeline = page.locator(".turn-activity-timeline")
    expect(timeline).to_be_visible()
    expect(timeline.locator(".turn-activity-block")).to_have_count(6)
    expect(timeline).not_to_contain_text("Instruction")
    expect(timeline).not_to_contain_text("Implemented the fix.")
    expect(timeline).to_contain_text("Development agent finished")
    expect(timeline).to_contain_text("Inspected project")
    expect(timeline).to_contain_text("Updated files")
    expect(timeline).to_contain_text("src/menu.js")
    expect(timeline).to_contain_text("+4")
    expect(timeline).to_contain_text("-2")
    expect(timeline).to_contain_text("Tests passed")
    expect(timeline).to_contain_text("Ran command")
    expect(timeline).to_contain_text("Browser check finished")
    expect(timeline).to_contain_text("python -m pytest -q tests/ui/test_menu.py")
    expect(timeline.locator(".turn-activity-content:visible")).to_have_count(0)
    timeline.get_by_text("Inspected project", exact=True).click()
    expect(timeline.locator(".turn-activity-content:visible")).to_contain_text("src/menu.js")
    page.screenshot(path="/private/tmp/development-completed-activity-desktop.png", full_page=True)

    expect(page.locator("#task-inspector")).to_be_hidden()
    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    page.wait_for_timeout(250)
    expect(timeline).to_be_visible()
    summary_height = timeline.locator(".turn-event-main, summary").first.evaluate("node => node.getBoundingClientRect().height")
    assert summary_height >= 40
    page.screenshot(path="/private/tmp/development-completed-activity-mobile.png", full_page=True)


def test_url_activity_is_single_and_clickable(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, url_activity=True)

    page.goto(URL)

    activity = page.locator(".activity-row").filter(has_text="Opened URL").first
    link = activity.get_by_role("link", name=TEST_BASE_URL + "/chat/")
    expect(link).to_be_visible()
    expect(link).to_have_attribute("href", TEST_BASE_URL + "/chat/")
    expect(link).to_have_attribute("target", "_blank")
    expect(activity.locator(".activity-detail")).to_have_count(0)
    page.screenshot(path="/private/tmp/development-clickable-url-activity.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#task-inspector").evaluate("node => node.classList.remove('open')")
    page.locator("#studio-shell").evaluate("node => node.classList.add('inspector-closed')")
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    page.wait_for_timeout(250)
    expect(link).to_be_visible()
    assert page.locator("#conversation").evaluate("node => node.scrollWidth <= node.clientWidth")
    page.screenshot(path="/private/tmp/development-clickable-url-activity-compact.png", full_page=True)


def test_durable_activity_replaces_matching_legacy_tool_message(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(
        page,
        requests,
        assistant_markdown=True,
        url_activity=True,
        durable_url_activity=True,
    )

    page.goto(URL)

    timeline = page.locator(".turn-activity-timeline")
    expect(timeline.locator(".turn-event-row")).to_have_count(1)
    expect(timeline).to_contain_text("Browser check finished")
    expect(timeline).to_contain_text(f"Opened URL: {TEST_BASE_URL}/chat/")
    expect(page.locator(".activity-group .activity-row")).to_have_count(0)


def test_ticket_lane_notices_render_as_compact_status_events(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, lane_move_notice=True)
    page.goto(URL)

    notice = page.locator(".thread-status-event")
    expect(notice).to_have_count(1)
    expect(notice).to_contain_text("Moved: In progress → QA")
    expect(page.locator("#message-list")).not_to_contain_text("Ticket #42")
    expect(notice).to_have_css("font-size", "9px")
    page.screenshot(path="/private/tmp/development-thread-lane-status.png", full_page=True)
    page.screenshot(path="/private/tmp/development-response-formatting.png", full_page=True)
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_missing_turn_duration_does_not_render_fake_zero_time(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests, assistant_missing_duration=True)
    page.goto(URL)

    timing = page.locator(".message-timing").last
    expect(timing).to_be_visible()
    expect(timing).not_to_contain_text("00:00:00")
    expect(page.locator('.message-markdown a[href="http://localhost:5174/"]')).to_be_visible()
    expect(page.locator('.message-markdown a[href="http://localhost:5174/"] code')).to_have_text("http://localhost:5174/")


def test_compact_response_actions_and_change_review_are_operable(page: Page):
    requests: list[tuple[str, str]] = []
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error" and "WebSocket connection" not in message.text
        else None,
    )
    _install_api(page, requests, assistant_markdown=True, direct_execution=True)
    page.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => { window.__copiedResponse = value; }}})")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(URL)

    card = page.locator(".turn-changes")
    expect(card).to_be_visible()
    expect(card).to_contain_text("Edited 2 files")
    expect(card).to_contain_text("+11")
    expect(card).to_contain_text("-3")
    page.locator("[data-copy-message]").last.click()
    assert page.evaluate("window.__copiedResponse").startswith("## Result")
    expect(page.locator("[data-copy-message]").last).to_have_attribute("aria-label", "Copy response")
    expect(page.locator("[data-copy-message]").last.locator("svg")).to_have_count(1)
    expect(page.locator("[data-fork-current]")).to_have_attribute("title", re.compile("does not create a Git branch", re.I))
    expect(page.locator("[data-fork-current]")).to_have_attribute("aria-label", "Fork thread")
    expect(page.locator("[data-fork-current] svg")).to_have_count(1)
    expect(page.locator(".message-timing").last).to_contain_text("00:02:34")
    footer = page.locator(".message-footer").last.bounding_box()
    actions = page.locator(".message-actions").last.bounding_box()
    timing = page.locator(".message-timing").last.bounding_box()
    assert footer and actions and timing
    assert actions["x"] < timing["x"]
    expect(page.locator(".turn-change-list")).to_be_visible()
    expect(page.locator(".turn-change-list thead")).to_contain_text("File")
    expect(page.locator(".turn-change-list thead")).to_contain_text("Added")
    expect(page.locator(".turn-change-list thead")).to_contain_text("Removed")
    expect(page.locator(".turn-change-row").first).to_contain_text("index.html")
    expect(page.locator(".turn-change-row").first).to_contain_text("+7")
    expect(page.locator(".turn-change-row").first).to_contain_text("-1")
    page.locator("[data-turn-changes-toggle]").click()
    expect(page.locator(".turn-change-list")).to_be_hidden()
    page.locator("[data-turn-changes-toggle]").click()
    expect(page.locator(".turn-change-list")).to_be_visible()
    expect(page.locator(".turn-change-row")).to_have_count(2)
    page.screenshot(path="/private/tmp/development-compact-response-actions.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    page.locator("#studio-sidebar").evaluate("node => node.classList.remove('open')")
    page.wait_for_timeout(250)
    dimensions = page.locator("body").evaluate("body => ({body: body.scrollWidth, viewport: document.documentElement.clientWidth})")
    assert dimensions["body"] <= dimensions["viewport"], dimensions
    expect(page.locator(".message-actions").last).to_have_css("opacity", "1")
    page.locator("#conversation").evaluate("node => { node.scrollTop = node.scrollHeight; }")
    page.wait_for_timeout(100)
    page.screenshot(path="/private/tmp/development-change-table-compact.png")
    page.set_viewport_size({"width": 1440, "height": 900})

    page.locator("[data-turn-review]").click()
    expect(page.locator("#task-inspector")).to_be_visible()
    expect(page.locator('.inspector-tabs button[data-tab="changes"]')).to_have_class(re.compile("active"))
    expect(page.locator(".change-file-row")).to_have_count(2)
    expect(page.locator(".change-review-summary")).to_contain_text("2 files changed")
    page.locator('.change-file-row[data-review-file="index.html"]').click()
    expect(page.locator(".change-file-review-header")).to_contain_text("index.html")
    expect(page.locator(".diff-line.deletion .diff-line-number").first).to_have_text("1")
    expect(page.locator(".diff-line.addition .diff-line-number").nth(1)).to_have_text("1")
    expect(page.locator(".diff-line.deletion code")).to_have_text("<h1>Menu</h1>")
    expect(page.locator(".diff-line.addition code")).to_have_text('<h1 class="menu">Menu</h1>')
    page.get_by_role("button", name="Copy file diff").click()
    assert page.evaluate("window.__copiedResponse").startswith("diff --git a/index.html")
    page.screenshot(path="/private/tmp/development-task-details-file-diff.png", full_page=True)
    page.set_viewport_size({"width": 375, "height": 812})
    review_dimensions = page.locator("body").evaluate("body => ({body: body.scrollWidth, viewport: document.documentElement.clientWidth})")
    assert review_dimensions["body"] <= review_dimensions["viewport"], review_dimensions
    expect(page.locator(".change-file-review-header")).to_be_visible()
    page.screenshot(path="/private/tmp/development-task-details-file-diff-compact.png", full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.get_by_role("button", name="Back to changed files").click()
    expect(page.locator(".change-file-row")).to_have_count(2)
    page.screenshot(path="/private/tmp/development-task-details-review.png", full_page=True)

    page.locator("[data-turn-undo]").click()
    expect(page.locator("#decisions-confirm-modal")).to_be_visible()
    page.get_by_role("button", name="Undo changes", exact=True).click()
    expect(card).to_have_count(0)
    assert ("POST", "/api/workflows/studio/tasks/17/execution/changes/undo") in requests
    assert console_errors == [], console_errors
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


@pytest.mark.parametrize("width,height,label", [(375, 812, "compact"), (768, 900, "tablet"), (1440, 900, "desktop")])
def test_responsive_layout_has_no_overflow_or_unnamed_controls(page: Page, width: int, height: int, label: str):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(URL)

    dimensions = page.locator("body").evaluate(
        "body => ({body: body.scrollWidth, viewport: document.documentElement.clientWidth})"
    )
    assert dimensions["body"] <= dimensions["viewport"], dimensions

    controls = page.locator("button:visible, a:visible, input:visible, select:visible, textarea:visible").evaluate_all(
        """els => els.map(el => {
            const label = el.getAttribute('aria-label') || el.getAttribute('title') ||
                (el.labels && el.labels.length ? Array.from(el.labels).map(item => item.textContent.trim()).join(' ') : '') ||
                el.textContent.trim() || el.getAttribute('placeholder') || '';
            return {tag: el.tagName, id: el.id, label: label.trim()};
        })"""
    )
    unnamed = [control for control in controls if not control["label"]]
    assert unnamed == [], unnamed

    page.screenshot(path=f"/private/tmp/development-harness-{label}.png", full_page=True)
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement && document.activeElement.tagName")
    assert focused in {"A", "BUTTON", "INPUT", "SELECT", "TEXTAREA"}
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_file_drop_is_scoped_to_composer_and_renders_image_preview(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.goto(URL)

    page.locator(".studio-main").evaluate(
        """node => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="#ff7a1a"/></svg>'], 'reference.svg', {type: 'image/svg+xml'}));
            node.dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true, dataTransfer: transfer}));
        }"""
    )
    expect(page.locator("#composer-drop-overlay")).to_be_hidden()

    page.locator("#studio-composer").evaluate(
        """node => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="#ff7a1a"/></svg>'], 'reference.svg', {type: 'image/svg+xml'}));
            node.dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true, dataTransfer: transfer}));
        }"""
    )
    expect(page.locator("#composer-drop-overlay")).to_be_visible()
    expect(page.locator("#composer-drop-overlay")).to_contain_text("Drop files into this prompt")
    page.screenshot(path="/private/tmp/development-composer-drop-valid.png", full_page=True)

    page.locator("#studio-composer").evaluate(
        """node => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="#ff7a1a"/></svg>'], 'reference.svg', {type: 'image/svg+xml'}));
            node.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer}));
        }"""
    )
    expect(page.locator("#composer-context .context-chip")).to_contain_text("reference.png")
    expect(page.locator("#composer-context .context-chip-preview")).to_be_visible()
    expect(page.locator("#composer-drop-overlay")).to_be_hidden()
    page.screenshot(path="/private/tmp/development-composer-image-preview.png", full_page=True)
    assert ("POST", "/api/workflows/studio/attachments") in requests
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_folder_drop_on_sidebar_registers_board_and_project(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.goto(URL)

    page.locator("#studio-sidebar").evaluate(
        """node => {
            const entry = {isDirectory: true, isFile: false, name: 'Dragged project'};
            const transfer = {
                types: ['Files'],
                items: [{kind: 'file', type: '', webkitGetAsEntry: () => entry, getAsFile: () => ({name: 'Dragged project', path: '/tmp/dragged-project'})}],
                files: [],
                getData: () => '',
            };
            const event = new Event('dragenter', {bubbles: true, cancelable: true});
            Object.defineProperty(event, 'dataTransfer', {value: transfer});
            node.dispatchEvent(event);
        }"""
    )
    expect(page.locator("#sidebar-drop-overlay")).to_be_visible()
    page.screenshot(path="/private/tmp/development-sidebar-folder-drop.png", full_page=True)

    page.locator("#studio-sidebar").evaluate(
        """node => {
            const entry = {isDirectory: true, isFile: false, name: 'Dragged project'};
            const transfer = {
                types: ['Files'],
                items: [{kind: 'file', type: '', webkitGetAsEntry: () => entry, getAsFile: () => ({name: 'Dragged project', path: '/tmp/dragged-project'})}],
                files: [],
                getData: () => '',
            };
            const event = new Event('drop', {bubbles: true, cancelable: true});
            Object.defineProperty(event, 'dataTransfer', {value: transfer});
            node.dispatchEvent(event);
        }"""
    )
    page.wait_for_timeout(250)
    assert ("POST", "/api/tickets/boards") in requests
    expect(page.locator("#sidebar-drop-overlay")).to_be_hidden()
    assert not any(method == "UNMOCKED" for method, _ in requests), requests


def test_image_drop_turns_red_for_a_model_without_vision(page: Page):
    requests: list[tuple[str, str]] = []
    _install_api(page, requests)
    page.goto(URL)
    page.locator("#model-button").click()
    page.locator('[data-model-pane="provider"]').click()
    page.get_by_role("menuitemradio", name=re.compile("Anthropic")).click()
    page.get_by_role("menuitemradio", name=re.compile("Claude Test")).click()
    page.locator("#model-dialog").evaluate("node => node.close()")
    page.wait_for_timeout(100)

    page.locator("#studio-composer").evaluate(
        """node => {
            const transfer = new DataTransfer();
            transfer.items.add(new File(['image'], 'reference.png', {type: 'image/png'}));
            node.dispatchEvent(new DragEvent('dragenter', {bubbles: true, cancelable: true, dataTransfer: transfer}));
        }"""
    )
    overlay = page.locator("#composer-drop-overlay")
    expect(overlay).to_be_visible()
    expect(overlay).to_have_class(re.compile(r"\bis-invalid\b"))
    expect(overlay).to_contain_text("This model cannot read images")
    assert "255, 98, 90" in overlay.evaluate("node => getComputedStyle(node).borderColor")
    page.screenshot(path="/private/tmp/development-composer-drop-invalid.png", full_page=True)
