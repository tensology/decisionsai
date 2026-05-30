"""Unit tests for board learned rules context helpers."""

from __future__ import annotations

import contextlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from distr.core.db import Base


def _factory(tmp_path):
    import distr.core.db.hermes  # noqa: F401

    db_path = tmp_path / "learned_rules.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextlib.contextmanager
def _session_ctx(factory):
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_build_learned_rules_context_includes_enabled_board_rules(tmp_path):
    from distr.core.hermes import build_learned_rules_context, record_learning_signal

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    from unittest.mock import patch

    with patch("distr.core.hermes.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_learning_signal(
            scope="board",
            scope_id=7,
            rule_type="validation_failure",
            summary="Always run npm test before marking UI tickets complete.",
            payload={"verdict": "fail"},
        )
        record_learning_signal(
            scope="board",
            scope_id=7,
            rule_type="ide_iteration",
            summary="Report files changed and tests run when returning from IDE.",
            payload={"run_id": 1},
        )
        context = build_learned_rules_context(7)

    assert "[BOARD LEARNED RULES]" in context
    assert "npm test" in context
    assert "files changed" in context


def test_build_standards_context_appends_board_rules(tmp_path):
    from distr.core.hermes import record_learning_signal
    from distr.core.workflow.standards_memory import build_standards_context

    factory = _factory(tmp_path)

    def get_session():
        return _session_ctx(factory)

    from unittest.mock import patch

    with patch("distr.core.hermes.get_session", get_session), patch("distr.core.db.get_session", get_session):
        record_learning_signal(
            scope="board",
            scope_id=3,
            rule_type="validation_failure",
            summary="Do not skip browser validation for frontend tickets.",
        )
        context = build_standards_context("Follow ticket instructions carefully.", board_id=3)

    assert "Follow ticket instructions carefully." in context
    assert "[UNIVERSAL WORKFLOW QUALITY STANDARDS]" in context
    assert "browser validation" in context
