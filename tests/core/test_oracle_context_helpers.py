"""Focused tests for distr.core.agent.services.llm.oracle_context (2026-10-22)."""
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from distr.core.agent.services.llm import oracle_context as oc


def test_strip_rag_block_returns_original_user_text():
    raw = f"{oc.RAG_HEADER}\n[wing/room] snippet{oc.USER_REQUEST_MARKER}fix the login bug"
    assert oc.strip_rag_block(raw) == "fix the login bug"
    for legacy in oc.LEGACY_RAG_HEADERS:
        assert oc.strip_rag_block(f"{legacy}\nx{oc.USER_REQUEST_MARKER}hello") == "hello"


def test_strip_rag_block_leaves_other_content_alone():
    assert oc.strip_rag_block("plain text") == "plain text"
    assert oc.strip_rag_block(f"{oc.RAG_HEADER} no marker") == f"{oc.RAG_HEADER} no marker"
    parts = [{"type": "text", "text": "x"}]
    assert oc.strip_rag_block(parts) is parts
    assert oc.strip_rag_block(None) is None


def test_strip_rag_block_uses_last_marker():
    raw = f"{oc.RAG_HEADER}\nA{oc.USER_REQUEST_MARKER}quoted{oc.USER_REQUEST_MARKER}real"
    assert oc.strip_rag_block(raw) == "real"


def test_filter_rag_snippets_drops_seed_corpus_and_unrelated():
    ctx = "\n---\n".join([
        "[seed_corpus_pack/x] kanban board workflow tickets",
        "[decisions/dev] kanban board workflow tickets for project",
        "[decisions/misc] spotify playlist music",
    ])
    out = oc.filter_rag_snippets("show the kanban board tickets", ctx)
    assert "seed_corpus_pack" not in out
    assert "[decisions/dev]" in out
    assert "spotify" not in out


def test_filter_rag_snippets_short_query_ratio_and_empty():
    assert oc.filter_rag_snippets("", "[a/b] x") == ""
    assert oc.filter_rag_snippets("hello", "") == ""
    assert oc.filter_rag_snippets("the and", "[a/b] the and") == ""  # only stop words
    assert "[a/b]" in oc.filter_rag_snippets("mempalace", "[a/b] mempalace setup notes")


def test_current_thread_store_roundtrip():
    oc.set_current_development_thread({"thread_id": 5, "title": "t"})
    got = oc.get_current_development_thread()
    got["thread_id"] = 99
    assert oc.get_current_development_thread()["thread_id"] == 5  # returns a copy
    oc.set_current_development_thread(None)
    assert oc.get_current_development_thread() == {}


def test_format_context_line():
    assert oc.format_context_line(None) == ""
    line = oc.format_context_line({"thread_id": 7, "title": 'Fix "x"\nnow', "project_id": 3, "project_name": "Decisions"})
    assert line.startswith(oc.CONTEXT_LINE_PREFIX + "Decisions (id 3)")
    assert '7 "Fix \'x\' now"' in line
    line2 = oc.format_context_line({"thread_id": 1, "title": "", "project_id": None, "project_name": ""})
    assert "unknown;" in line2 and "(id" not in line2


@pytest.fixture()
def session():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT)"))
        c.execute(text("CREATE TABLE chats (id INTEGER PRIMARY KEY, title TEXT, project_id INTEGER, parent_id INTEGER, additional_context TEXT)"))
    with Session(eng) as s:
        yield s


def test_derive_current_development_thread(session):
    session.execute(text("INSERT INTO projects VALUES (3, 'Decisions')"))
    session.execute(text("INSERT INTO chats VALUES (40, 'Dev thread', 3, NULL, NULL)"))
    session.execute(text("INSERT INTO chats VALUES (41, 'Other', NULL, NULL, NULL)"))
    session.execute(text("INSERT INTO chats VALUES (100, 'note old', NULL, 1, :c)"), {"c": json.dumps({"oracle_note": True, "source_thread_id": 41})})
    session.execute(text("INSERT INTO chats VALUES (101, 'note new', NULL, 1, :c)"), {"c": json.dumps({"oracle_note": True, "source_thread_id": "40"})})
    session.execute(text("INSERT INTO chats VALUES (102, 'junk', NULL, 1, 'oracle_note not json')"))
    assert oc.derive_current_development_thread(session, 1) == {
        "thread_id": 40, "title": "Dev thread", "project_id": 3, "project_name": "Decisions"}
    assert oc.derive_current_development_thread(session, 2) is None


def test_derive_missing_thread_returns_none(session):
    session.execute(text("INSERT INTO chats VALUES (100, 'n', NULL, 1, :c)"), {"c": json.dumps({"oracle_note": True, "source_thread_id": 999})})
    assert oc.derive_current_development_thread(session, 1) is None
