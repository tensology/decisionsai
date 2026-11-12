"""Incoming-origin Development replies leave the client message to Message Client."""
import pytest

from distr.core.db import Chat, get_session
from distr.core.db.projects import Project
from distr.core.incoming import source_context as sc


@pytest.fixture
def source_thread(tmp_path):
    with get_session() as db:
        project = Project(name="Client reply guidance", folder_location=str(tmp_path))
        db.add(project); db.flush()
        chat = Chat(title="Clarify banner", project_id=project.id)
        db.add(chat); db.commit()
        cid = int(chat.id)
    sc.register_source_context(cid, {"source": "whatsapp", "source_thread_id": "guidance-fixture@lid",
                                     "project_id": int(project.id), "message_ids": ["guidance-msg"]})
    yield cid


def test_source_thread_prompt_forbids_inline_client_draft_and_screenshot_narration(source_thread):
    text = sc.thread_source_instructions(source_thread)
    assert "Message Client" in text
    assert "Do not write a draft message to the client" in text
    assert "do not describe or transcribe" in text
    assert "what to do next" in text


def test_threads_without_source_get_no_client_guidance(tmp_path):
    with get_session() as db:
        chat = Chat(title="Plain thread")
        db.add(chat); db.commit()
        cid = int(chat.id)
    assert sc.thread_source_instructions(cid) == ""
