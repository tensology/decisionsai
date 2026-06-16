import json
import sqlite3
from types import SimpleNamespace

from distr.core.agent.services.llm.fast_action_detector import ActionType, detect_fast_action
from distr.core.external_agent_context import (
    build_agent_visibility_answer,
    build_codex_thread_context,
    build_cursor_thread_context,
    format_codex_thread_context_for_prompt,
    format_external_agent_context_for_prompt,
    list_codex_threads,
    list_cursor_threads,
    list_cursor_workspaces,
)
from distr.core.agent.services.llm.text_utils import clean_text_for_tts


def test_codex_threads_are_read_from_local_state_db(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    db = codex_home / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        create table threads (
            id text,
            updated_at integer,
            updated_at_ms integer,
            source text,
            cwd text,
            title text,
            archived integer,
            first_user_message text,
            preview text,
            model text
        )
        """
    )
    conn.execute(
        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "thread-1",
            1770000000,
            1770000000000,
            "vscode",
            "/repo/app",
            "Fix the app",
            0,
            "Fix the app",
            "Fix the app preview",
            "gpt-5.5",
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    threads = list_codex_threads(limit=1)

    assert threads[0]["id"] == "thread-1"
    assert threads[0]["cwd"] == "/repo/app"
    assert threads[0]["title"] == "Fix the app"
    assert "rollout_path" in threads[0]


def test_codex_thread_context_reads_matching_rollout_transcript(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    rollout = codex_home / "rollout-thread-1.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "payload": {
                            "type": "message",
                            "role": "developer",
                            "content": [{"type": "input_text", "text": "<permissions instructions>noise</permissions instructions>"}],
                        }
                    }
                ),
                json.dumps(
                    {
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "<environment_context>noise</environment_context>"}],
                        }
                    }
                ),
                json.dumps(
                    {
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "PAGES NOT RUNNING PROPERLY."}],
                        }
                    }
                ),
                json.dumps({"payload": {"type": "function_call", "name": "exec_command"}}),
                json.dumps(
                    {
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "phase": "final_answer",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "I checked the Multisnack pages and found the app failing before render.",
                                }
                            ],
                        }
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    db = codex_home / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        create table threads (
            id text,
            updated_at integer,
            updated_at_ms integer,
            source text,
            cwd text,
            title text,
            archived integer,
            first_user_message text,
            preview text,
            model text,
            rollout_path text
        )
        """
    )
    conn.execute(
        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "thread-1",
            1770000000,
            1770000000000,
            "codex",
            "/Users/paul/development/WORK/CRYSTALLOGIC/dpp.multisnack.co.za",
            "PAGES NOT RUNNING PROPERLY.",
            0,
            "PAGES NOT RUNNING PROPERLY.",
            "PAGES NOT RUNNING PROPERLY.",
            "gpt-5.5",
            str(rollout),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    context = build_codex_thread_context(query="multisnack pages issue", limit_messages=10)
    formatted = format_codex_thread_context_for_prompt(context)

    assert context["found"] is True
    assert context["project_name"] == "Multisnack"
    assert [message["role"] for message in context["messages"]] == ["user", "assistant"]
    assert "PAGES NOT RUNNING PROPERLY" in context["messages"][0]["text"]
    assert "environment_context" not in formatted
    assert "I found the Multisnack Codex conversation" in formatted
    assert "without you pasting it here" in formatted
    assert "REFERENCE:" in formatted
    assert "exec_command" in formatted


def test_codex_thread_context_generic_conversation_request_uses_latest_thread(tmp_path, monkeypatch):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    older_rollout = codex_home / "rollout-older.jsonl"
    newer_rollout = codex_home / "rollout-newer.jsonl"
    older_rollout.write_text(
        json.dumps({"payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Older task"}]}}),
        encoding="utf-8",
    )
    newer_rollout.write_text(
        json.dumps({"payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Newest DecisionsAI task"}]}}),
        encoding="utf-8",
    )
    db = codex_home / "state_5.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        create table threads (
            id text,
            updated_at integer,
            updated_at_ms integer,
            source text,
            cwd text,
            title text,
            archived integer,
            first_user_message text,
            preview text,
            model text,
            rollout_path text
        )
        """
    )
    conn.execute(
        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("older", 1770000000, 1770000000000, "codex", "/repo/older", "Older task", 0, "", "", "", str(older_rollout)),
    )
    conn.execute(
        "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "newer",
            1770000100,
            1770000100000,
            "codex",
            "/Users/paul/development/TENSOLOGY/DECISIONS",
            "Newest DecisionsAI task",
            0,
            "",
            "",
            "",
            str(newer_rollout),
        ),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    user_request = (
        "If I were to ask you to actually try and work with one of my conversations "
        "inside of codecs, are you able to do that?"
    )

    context = build_codex_thread_context(query=user_request)

    assert context["found"] is True
    assert context["thread"]["id"] == "newer"
    assert context["project_name"] == "DecisionsAI"


def test_codex_thread_context_tool_returns_voice_first_reference(monkeypatch):
    from distr.core.agent.tools.system.codex_thread_context import CodexThreadContextTool

    monkeypatch.setattr(
        "distr.core.external_agent_context.build_codex_thread_context",
        lambda **kwargs: {
            "found": True,
            "thread": {
                "id": "thread-1",
                "cwd": "/Users/paul/development/TENSOLOGY/DECISIONS",
                "title": "Codex conversation handoff",
            },
            "project_name": "DecisionsAI",
            "activity_hint": "Codex conversation handoff",
            "messages": [{"role": "user", "text": "Turn this into a ticket."}],
            "tool_calls": [],
            "warning": "",
            "alternatives": [],
        },
    )

    result = CodexThreadContextTool()._run(query="work with my Codex conversation")

    assert result.startswith("I found the DecisionsAI Codex conversation.")
    assert "without you pasting it here" in result
    assert "REFERENCE:" in result


def test_cursor_workspaces_are_read_from_workspace_storage(tmp_path, monkeypatch):
    storage = tmp_path / "workspaceStorage"
    workspace = storage / "abc123"
    workspace.mkdir(parents=True)
    (workspace / "workspace.json").write_text(
        json.dumps({"folder": "file:///Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_WORKSPACE_STORAGE", str(storage))

    workspaces = list_cursor_workspaces(limit=1)

    assert workspaces[0]["folder"] == "/Users/paul/development/TENSOLOGY/DECISIONS/DecisionsAI"


def test_cursor_threads_are_read_from_local_agent_transcripts(tmp_path, monkeypatch):
    projects_root = tmp_path / "projects"
    slug = "repo-app"
    transcript_id = "chat-thread-1"
    transcript_dir = projects_root / slug / "agent-transcripts" / transcript_id
    transcript_dir.mkdir(parents=True)
    transcript_path = transcript_dir / f"{transcript_id}.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "<user_query>\nFix the dictation drop bug\n</user_query>",
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "role": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "I found the issue in remote routing."},
                                {"type": "tool_use", "name": "Grep", "input": {}},
                            ]
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CURSOR_PROJECTS_HOME", str(projects_root))

    threads = list_cursor_threads(folder="/repo/app", limit=5)
    assert threads[0]["id"] == transcript_id
    assert "dictation" in threads[0]["title"].lower()

    context = build_cursor_thread_context(folder="/repo/app", query="dictation", limit_messages=5)
    assert context["found"] is True
    assert context["messages"][0]["role"] == "user"
    assert "dictation drop bug" in context["messages"][0]["content"]
    assert context["messages"][1]["content"].startswith("I found the issue")
    assert "Grep" in context["tool_calls"]


def test_external_agent_context_formats_for_prompt():
    text = format_external_agent_context_for_prompt({
        "codex_threads": [{"updated_at": "2026-06-01T18:36:17+00:00", "cwd": "/repo", "title": "Audit Codex visibility"}],
        "cursor_workspaces": [{"updated_at": "2026-06-01T18:20:00+00:00", "folder": "/repo"}],
    })

    assert "external_agent_context" in text
    assert "Audit Codex visibility" in text
    assert "cursor_workspaces" in text


def test_codex_cursor_visibility_question_routes_to_developer_context():
    action = detect_fast_action("Can you answer my question? Can you see what I’m working on Inside of codecs?")

    assert action.action_type == ActionType.DEVELOPER_CONTEXT
    assert action.tool_name == "developer_context"
    assert action.response_type == "developer_context"


def test_codex_cursor_visibility_question_matches_when_tool_name_comes_first():
    action = detect_fast_action("Can you see cursor or codecs and see what I'm working on?")

    assert action.action_type == ActionType.DEVELOPER_CONTEXT
    assert action.tool_name == "developer_context"
    assert action.response_type == "developer_context"


def test_generic_fast_tool_matcher_skips_codex_cursor_visibility_questions():
    from distr.core.agent.tools.base import fast_tool_matcher
    from distr.core.agent.tools.system.project_tools import SwitchProjectTool

    tool = SwitchProjectTool()

    result = fast_tool_matcher(
        "Can you see cursor or codecs and see what I'm working on?",
        [tool],
        {tool.name: tool},
    )

    assert result is None


def test_switch_project_codex_misroute_returns_visibility_context(monkeypatch):
    from distr.core.agent.tools.system.project_tools import SwitchProjectTool

    monkeypatch.setattr(
        "distr.core.external_agent_context.build_agent_visibility_answer",
        lambda user_request="", max_chars=1800: "Yes. I can see recorded Codex work.",
    )

    result = SwitchProjectTool()._run(project_name="Inside of codecs")

    assert result == "Yes. I can see recorded Codex work."


def test_switch_project_text_arg_codex_misroute_returns_visibility_context(monkeypatch):
    from distr.core.agent.tools.system.project_tools import SwitchProjectTool

    monkeypatch.setattr(
        "distr.core.external_agent_context.build_agent_visibility_answer",
        lambda user_request="", max_chars=1800: "Yes. I can see recorded Codex work.",
    )

    result = SwitchProjectTool()._run(text="Can you see cursor or codecs and see what I'm working on?")

    assert result == "Yes. I can see recorded Codex work."


def test_agent_visibility_answer_is_conversational_not_raw_path_dump(monkeypatch):
    context = SimpleNamespace(
        active_project=SimpleNamespace(
            name="Tensology",
            folder_location="/Users/paul/development/TENSOLOGY/www.tensology.com",
        ),
        active_workflows=[],
        active_executions=[],
        external_agent_context={
            "codex_threads": [
                {
                    "cwd": "/Users/paul/development/TENSOLOGY/DECISIONS",
                    "title": "There are ways you can actually see all of the projects that are in codecs and cursor through their extensions, their plugins.",
                },
                {
                    "cwd": "/Users/paul/development/WORK/CRYSTALLOGIC/dpp.multisnack.co.za",
                    "title": "PAGES NOT RUNNING PROPERLY.",
                },
                {
                    "cwd": "/Users/paul/development/TENSOLOGY/DECISIONS",
                    "title": "Fix ElevenLabs TTS crackle",
                },
            ],
            "cursor_workspaces": [
                {"folder": "/Users/paul/development/TENSOLOGY/www.tensology.com"},
                {"folder": "/Users/paul/development/WORK/INTRICODE/www.player1sport.com"},
            ],
        },
    )
    monkeypatch.setattr("distr.core.developer_context.build_developer_context", lambda user_request="": context)

    answer = build_agent_visibility_answer("Can you see what I'm doing in Codex or Cursor?")
    spoken = clean_text_for_tts(answer, spoken_prose=True)

    assert "Right now, the active project is Tensology" in answer
    assert "Multisnack" in answer
    assert "Player1Sport" in answer
    assert "Codex and Cursor visibility" in answer
    assert "There are ways" not in answer
    assert "Recent Codex threads" not in answer
    assert "/Users/" not in answer
    assert "saved a file" not in spoken.lower()
