"""routee2e 2026-10-22. Run with HOME and DECISIONS_DB_DIR on a /tmp DB copy."""
import json
import os
from types import SimpleNamespace

import pytest

PROJECTS = [(7, "Spoortrack (Old)"), (9, "DecisionsAI"), (10, "Spoortrack"), (2, "Merrypak")]

ONE_SHOTS = [
    "create a file called routing-test-1.md in my Downloads folder with the word hello",
    "check my email",
    "transcribe this MP3: /tmp/x.mp3",
    "what time is it",
]
PROJECT_WORK = [
    ("in the DecisionsAI project, add a TODO comment to README explaining X", 9),
    ("create a new development thread in DecisionsAI called routing test", 9),
    ("fix the login bug in Spoortrack", 10),
]
ALL_PROMPTS = ONE_SHOTS + [p for p, _ in PROJECT_WORK] + ["new chat", "start over", "start a new conversation"]


@pytest.mark.parametrize("text", ONE_SHOTS)
def test_one_shots_stay_in_chat(text):
    from distr.core.workflow.oracle_routing_rule import classify_instruction
    assert classify_instruction(text, projects=PROJECTS)["route"] == "one_shot"


@pytest.mark.parametrize("text,pid", PROJECT_WORK)
def test_project_work_resolves_named_project(text, pid):
    from distr.core.workflow.oracle_routing_rule import classify_instruction
    out = classify_instruction(text, projects=PROJECTS)
    assert out == {"route": "project", "project_id": pid}


def test_in_thread_context_continues_thread():
    from distr.core.workflow.oracle_routing_rule import classify_instruction
    assert classify_instruction("check my email", in_dev_thread=True)["continue_current_thread"]


def test_longest_project_name_wins():
    from distr.core.workflow.oracle_routing_rule import resolve_named_project
    assert resolve_named_project("work on Spoortrack (Old) please", PROJECTS) == 7
    assert resolve_named_project("decisions ai thread", PROJECTS) == 9
    assert resolve_named_project("hello there", PROJECTS) is None


@pytest.mark.parametrize("text", ALL_PROMPTS)
def test_new_chat_guard_blocks_every_prompt(text):
    from distr.core.agent.services.llm.tool_execution_policy import (
        NEW_CHAT_BLOCK_REASON, tool_execution_block_reason)
    owner = SimpleNamespace()
    assert tool_execution_block_reason(owner, "new_chat", {}, text) == NEW_CHAT_BLOCK_REASON
    assert tool_execution_block_reason(owner, "open_page", {"page": "new chat"}, text) == NEW_CHAT_BLOCK_REASON
    assert tool_execution_block_reason(owner, "open_page", {"page": "development"}, text) != NEW_CHAT_BLOCK_REASON


def test_ide_thread_prompt_blocked_unless_ide_named():
    from distr.core.agent.services.llm.tool_execution_policy import (
        IDE_MISROUTE_REASON, tool_execution_block_reason)
    t = PROJECT_WORK[0][0]
    assert tool_execution_block_reason(SimpleNamespace(), "ide_thread", {"action": "prompt"}, t) == IDE_MISROUTE_REASON
    assert tool_execution_block_reason(SimpleNamespace(), "ide_thread", {"action": "prompt"}, "ask Codex to " + t) == ""
    assert tool_execution_block_reason(SimpleNamespace(), "ide_thread", {"action": "read"}, t) == ""


def test_read_only_health_check_not_blocked_by_no_action_phrase():
    from distr.core.agent.services.llm.tool_execution_policy import tool_execution_block_reason
    t = "check my email. Read-only: do not send, reply, delete or archive anything."
    assert tool_execution_block_reason(SimpleNamespace(), "google_workspace", {"action": "health"}, t) == ""


def test_file_operations_resolves_spoken_folder_prefix():
    from distr.core.agent.tools.files.file_operations import FileOperationsTool as T
    t = T.__new__(T)
    home = os.path.expanduser("~")
    assert T._resolve_path(t, "my downloads/routing-test-1.md") == os.path.join(home, "Downloads", "routing-test-1.md")


def test_audio_transcriber_recovers_path_from_message(tmp_path):
    from distr.core.agent.tools.media.audio_transcriber import AudioTranscriberTool as T
    t = T.__new__(T)
    out = T._run(t, last_user_message="transcribe this MP3: /nonexistent/zz.mp3")
    assert "No audio file specified" in out  # nonexistent path is not trusted


@pytest.mark.skipif(not str(os.environ.get("DECISIONS_DB_DIR", "")).startswith(("/tmp", "/private/tmp")),
                    reason="needs DECISIONS_DB_DIR on a /tmp DB copy")
def test_new_chat_tool_creates_zero_chats():
    from distr.core.agent.tools.chat.new_chat import NewChatTool
    from distr.core.db import Chat, get_session

    class CM:
        def create_chat(self, *a, **k):
            raise AssertionError("create_chat must never be called")
        def get_current_chat(self):
            return 1
    with get_session() as db:
        before = db.query(Chat).filter(Chat.parent_id.is_(None)).count()
    for text in ALL_PROMPTS:
        out = json.loads(NewChatTool(chat_manager=CM())._run(last_user_message=text))
        assert out["status"] == "refused"
    with get_session() as db:
        assert db.query(Chat).filter(Chat.parent_id.is_(None)).count() == before
