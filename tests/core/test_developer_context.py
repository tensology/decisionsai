from distr.core.developer_context import (
    DeveloperBoardContext,
    DeveloperContextAssembler,
    DeveloperProjectContext,
    DeveloperRuntimeContext,
    DeveloperSkillContext,
    DeveloperTicketContext,
    DeveloperWorkContext,
    DeveloperWorkflowContext,
    format_developer_context_dict_for_prompt,
)


def test_prompt_text_includes_project_board_tickets_workflows_and_skills():
    context = DeveloperWorkContext(
        runtime=DeveloperRuntimeContext(
            cwd="/workspace/app",
            current_chat_id=42,
            debug_mode=True,
            captured_at="2026-05-11T10:00:00",
        ),
        active_project=DeveloperProjectContext(
            id=7,
            name="DecisionsAI",
            description="Desktop agentic developer workflow",
            folder_location="/workspace/app",
        ),
        active_board=DeveloperBoardContext(
            id=3,
            name="Main Board",
            default_project_id=7,
            default_workflow_id=9,
            send_to_cli=True,
            lanes=[{"name": "Current", "ticket_count": 2}],
        ),
        active_tickets=[
            DeveloperTicketContext(
                id=11,
                title="Fix workflow routing",
                lane="Current",
                priority="high",
                workflow_status="running",
            )
        ],
        active_workflows=[
            DeveloperWorkflowContext(
                id=21,
                name="Ticket implementation",
                status="running",
                current_step_id=31,
                current_step_name="Run tests",
                ticket_id=11,
                live_agent_context={
                    "last_event_type": "user_steer",
                    "last_status": "observed",
                    "latest_user_steer": "Use browser validation before marking this done.",
                    "execution_session_id": 55,
                },
            )
        ],
        recommended_skills=[
            DeveloperSkillContext(
                name="webapp-testing",
                reason="The work likely needs browser/UI validation.",
            )
        ],
    )

    text = context.to_prompt_text()

    assert "active_project: #7 DecisionsAI" in text
    assert "active_board: #3 Main Board" in text
    assert "#11 Fix workflow routing" in text
    assert "Ticket implementation" in text
    assert "live_agent_context" in text
    assert "browser validation" in text
    assert "webapp-testing" in text


def test_build_is_defensive_when_fetchers_fail(monkeypatch):
    assembler = DeveloperContextAssembler()

    monkeypatch.setattr(assembler, "_fetch_active_project", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(assembler, "_fetch_active_board", lambda _project: None)
    monkeypatch.setattr(assembler, "_fetch_active_tickets", lambda _board, _chat_id: [])
    monkeypatch.setattr(assembler, "_fetch_active_workflows", lambda _board, _tickets: [])
    monkeypatch.setattr(assembler, "_recommend_skills", lambda _request: [])

    context = assembler.build({"agent_current_chat_id": "5"})

    assert context.runtime.current_chat_id == 5
    assert context.active_project is None
    assert "active project unavailable" in context.warnings


def test_build_collects_recommended_skills(monkeypatch):
    assembler = DeveloperContextAssembler()

    monkeypatch.setattr(assembler, "_fetch_active_project", lambda: None)
    monkeypatch.setattr(assembler, "_fetch_active_board", lambda _project: None)
    monkeypatch.setattr(assembler, "_fetch_active_tickets", lambda _board, _chat_id: [])
    monkeypatch.setattr(assembler, "_fetch_active_workflows", lambda _board, _tickets: [])
    monkeypatch.setattr(
        assembler,
        "_recommend_skills",
        lambda _request: [DeveloperSkillContext("systematic-debugging", "Logs or failures were mentioned.")],
    )

    context = assembler.build({}, user_request="check the logs for the failing workflow")

    assert context.recommended_skills == [
        DeveloperSkillContext("systematic-debugging", "Logs or failures were mentioned.")
    ]


def test_developer_context_tool_returns_prompt_summary(monkeypatch):
    from distr.core.agent.tools.system.developer_context import DeveloperContextTool

    context = DeveloperWorkContext(
        runtime=DeveloperRuntimeContext(cwd="/workspace/app", debug_mode=False),
        active_board=DeveloperBoardContext(id=1, name="Engineering"),
    )

    monkeypatch.setattr(
        "distr.core.developer_context.build_developer_context",
        lambda user_request="": context,
    )

    result = DeveloperContextTool()._run(user_request="make a ticket")

    assert "Developer workflow context:" in result
    assert "active_board: #1 Engineering" in result


def test_format_developer_context_dict_for_prompt_uses_stored_context():
    context = {
        "runtime": {"cwd": "/repo", "current_chat_id": 12, "debug_mode": True},
        "active_project": {"id": 2, "name": "DecisionsAI", "folder_location": "/repo"},
        "active_board": {
            "id": 4,
            "name": "Workflow Board",
            "source": "database",
            "default_project_id": 2,
            "default_workflow_id": 9,
            "send_to_cli": False,
            "lanes": [{"name": "Current", "ticket_count": 1}],
        },
        "active_tickets": [
            {"id": 8, "title": "Run ticket workflow", "lane": "Current", "priority": "high"}
        ],
        "active_workflows": [
            {
                "id": 14,
                "name": "Implementation",
                "status": "running",
                "ticket_id": 8,
                "live_agent_context": {
                    "last_event_type": "ide_iteration_completed",
                    "last_status": "completed",
                    "latest_terminal_summary": "Cursor updated the panel and ran tests.",
                },
            }
        ],
        "active_executions": [
            {
                "id": 21,
                "status": "running",
                "backend": "cursor_ide",
                "project_id": 2,
                "project_name": "DecisionsAI",
                "origin": "telegram",
                "instruction_preview": "Update the settings panel from Telegram.",
            }
        ],
    }

    text = format_developer_context_dict_for_prompt(context)

    assert "active_project: #2 DecisionsAI" in text
    assert "active_board: #4 Workflow Board" in text
    assert "workflow=9" in text
    assert "#8 Run ticket workflow" in text
    assert "ide_iteration_completed" in text
    assert "Cursor updated the panel" in text
    assert "active_project_executions" in text
    assert "Update the settings panel from Telegram" in text
