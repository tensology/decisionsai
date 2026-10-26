"""Project tools must speak naturally and keep technical detail behind REFERENCE:.

Regression for the "What projects are we working on?" turn that echoed
markdown headings like ``**Available Projects (11) - LIVE DATA:**`` into the
spoken answer instead of a plain sentence.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _clear_turn_binding():
    from distr.core.chat_manager import bind_active_chat_turn

    bind_active_chat_turn(0, None)
    yield
    bind_active_chat_turn(0, None)


def _fake_session(projects):
    session = MagicMock()
    query = session.query.return_value
    query.order_by.return_value.all.return_value = projects
    query.filter.return_value.first.return_value = projects[0] if projects else None
    query.all.return_value = projects
    session.close.return_value = None
    return session


def test_list_projects_returns_speakable_summary(monkeypatch):
    from distr.core.agent.tools.system.project_tools import ListProjectsTool

    projects = [
        SimpleNamespace(
            name="Jackcat",
            id=3,
            in_use=True,
            folder_location="/Users/paul/dev/jackcat",
            description="Internal tooling",
        ),
        SimpleNamespace(
            name="Merrypak",
            id=7,
            in_use=False,
            folder_location=None,
            description="",
        ),
    ]
    monkeypatch.setattr(
        "distr.core.db.get_session", lambda: _fake_session(projects)
    )

    out = ListProjectsTool()._run(text="What projects are we working on?")

    assert "REFERENCE:" in out
    spoken = out.split("REFERENCE:", 1)[0].strip()
    # Plain sentence for the voice, no markup or machine detail.
    assert "Jackcat" in spoken and "Merrypak" in spoken
    assert "**" not in spoken
    assert "ID:" not in spoken
    assert "/Users" not in spoken
    assert "LIVE DATA" not in spoken

    # The spoken form of the full result never carries the reference dump.
    from distr.core.agent.services.llm.text_utils import clean_text_for_tts

    spoken_tts = clean_text_for_tts(out)
    assert "Jackcat" in spoken_tts
    assert "ID:" not in spoken_tts
    assert "/Users" not in spoken_tts


def test_get_project_details_returns_speakable_summary(monkeypatch):
    from distr.core.agent.tools.system.project_tools import GetProjectDetailsTool

    project = SimpleNamespace(
        name="DecisionsAI",
        id=9,
        in_use=True,
        folder_location="/Users/paul/development/TENSOLOGY/DecisionsAI",
        description="Agent workspace",
        startup_instructions="./decisions\n",
    )
    monkeypatch.setattr(
        "distr.core.db.get_session", lambda: _fake_session([project])
    )

    out = GetProjectDetailsTool()._run(text="check project DecisionsAI")

    assert "REFERENCE:" in out
    spoken = out.split("REFERENCE:", 1)[0].strip()
    assert "DecisionsAI" in spoken
    assert "active" in spoken
    assert "folder" in spoken.lower()
    assert "**" not in spoken
    assert "/Users" not in spoken
