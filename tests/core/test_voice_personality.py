"""The selected custom voice's personality must reach the agent persona."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import distr.core.db  # noqa: F401
from distr.core.db import Base, CustomVoice


@pytest.fixture()
def db_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("distr.core.db.get_session", lambda: factory())
    return factory


def _session_stub():
    from distr.core.agent.session import AgentSession

    stub = AgentSession.__new__(AgentSession)
    stub._custom_voice_personality = ""
    stub.logger = logging.getLogger("test.voice_personality")
    return stub


def _voice(factory, *, provider: str, voice_id: str, personality: str, status: str = "ready") -> int:
    with factory() as db:
        row = CustomVoice(
            name="Hayley Williams",
            provider=provider,
            provider_voice_id=voice_id,
            status=status,
            personality=personality,
        )
        db.add(row)
        db.commit()
        return int(row.id)


def test_personality_loads_when_the_provider_label_differs(db_factory):
    from distr.core.agent.session import AgentSession

    _voice(
        db_factory,
        provider="fishaudio",
        voice_id="8cb180b1ab9949e2bb59e1aaf9fa062c",
        personality="Warm, dry, cuts to the chase.",
    )
    stub = _session_stub()

    # The chat may carry a human label rather than the provider id.
    AgentSession._load_custom_voice_personality(
        stub, "Fish Audio (Online)", "8cb180b1ab9949e2bb59e1aaf9fa062c"
    )

    assert stub._custom_voice_personality == "Warm, dry, cuts to the chase."


def test_personality_loads_from_a_custom_voice_id(db_factory):
    from distr.core.agent.session import AgentSession

    voice_db_id = _voice(
        db_factory,
        provider="coqui",
        voice_id="ignored",
        personality="Patient and precise.",
    )
    stub = _session_stub()

    AgentSession._load_custom_voice_personality(stub, "coqui", f"custom_{voice_db_id}")

    assert stub._custom_voice_personality == "Patient and precise."


def test_a_non_ready_voice_is_not_used(db_factory):
    from distr.core.agent.session import AgentSession

    _voice(
        db_factory,
        provider="fishaudio",
        voice_id="hash-1",
        personality="Should not load.",
        status="pending",
    )
    stub = _session_stub()

    AgentSession._load_custom_voice_personality(stub, "fishaudio", "hash-1")

    assert stub._custom_voice_personality == ""


def test_a_missing_personality_leaves_the_persona_empty(db_factory):
    from distr.core.agent.session import AgentSession

    _voice(db_factory, provider="fishaudio", voice_id="hash-2", personality="")
    stub = _session_stub()

    AgentSession._load_custom_voice_personality(stub, "fishaudio", "hash-2")

    assert stub._custom_voice_personality == ""


def test_no_voice_id_is_a_no_op(db_factory):
    from distr.core.agent.session import AgentSession

    stub = _session_stub()
    AgentSession._load_custom_voice_personality(stub, "fishaudio", "")
    assert stub._custom_voice_personality == ""
