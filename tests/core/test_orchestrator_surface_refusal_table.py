"""Regression table (2026-10-22 refusal fix): plain questions stay chat, project work that
contains what/how/why goes to development, explicit new-chat-in-project requests go to development."""
import pytest

from distr.core.agent.services.llm.fast_action_detector import ActionType, FastActionDetector
from distr.core.workflow.development_threads import orchestrator_surface

CASES = [
("what is the point of this project?","chat"),
("how does the router work in DecisionsAI?","chat"),
("why did the build fail yesterday?","chat"),
("who wrote this module?","chat"),
("tell me a story","chat"),
("explain the kanban flow","chat"),
("what's the status of the SpoorTrack project?","chat"),
("where are the logs stored?","chat"),
("can you tell me what the project does?","chat"),
("just chat with me about the roadmap","chat"),
("how are you?","chat"),
("update the README with what the project does","development"),
("fix the bug where the router refuses what questions","development"),
("add a section explaining how the installer works","development"),
("document why the cache is disabled in the README","development"),
("write tests for how the guard handles new chats","development"),
("implement a page that shows what the agent is doing","development"),
("change the prompt so it explains why it refused","development"),
("refactor how settings are loaded","development"),
("please update the changelog with what changed today","development"),
("create a new chat in the DecisionsAI project and ask what the point of this project is","development"),
("start a new chat in SpoorTrack and ask how auth works","development"),
("open a new thread in DecisionsAI and ask why tests are slow","development"),
("in a new chat, what is 2+2?","chat"),
("stay in chat and explain how the build works","chat"),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_orchestrator_surface_table(text, expected):
    assert orchestrator_surface(text) == expected


@pytest.mark.parametrize("text", [
    "Can you create a new chat in the Decisions AI project and ask what the point of this project is?",
    "start a new chat in SpoorTrack and ask how auth works",
    "what is the point of this project?",
])
def test_fast_path_does_not_hijack_into_refused_new_chat(text):
    assert FastActionDetector().detect(text).action_type != ActionType.NEW_CHAT
