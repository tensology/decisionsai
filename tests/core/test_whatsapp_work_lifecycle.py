from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text

from distr.core.kanban import whatsapp_work_lifecycle as lifecycle


def _isolated_engine(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lifecycle.db'}")
    monkeypatch.setattr(lifecycle, "engine", engine)
    lifecycle.ensure_tables()
    return engine


def _settle_to_draft(monkeypatch, ticket_id: int, run_id: int = 8, summary: str = "Checkout now passes browser validation."):
    """Drive verify OK → deploy confirm → client draft (no live Telegram)."""
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.get.return_value = SimpleNamespace(title="Fix the checkout button")
    monkeypatch.setattr(lifecycle, "get_session", lambda: session)
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(lifecycle, "notify_telegram_verification", lambda *_a, **_k: True)
    monkeypatch.setattr(lifecycle, "notify_telegram_deploy", lambda *_a, **_k: True)
    monkeypatch.setattr(lifecycle, "notify_telegram_review", lambda *_a, **_k: True)
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_clear_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "record_verification_lesson", lambda **kwargs: {
        "recorded": True, "lesson": "lesson",
    })

    gate = lifecycle.begin_post_completion_gates(
        ticket_id=ticket_id, run_id=run_id, status="completed", result_summary=summary,
    )
    assert gate and gate["gate_kind"] == "verification"
    deploy = lifecycle.handle_telegram_reply(f"wv:{gate['token']}:ok", chat_id=99)
    assert deploy["action"] == "verification_ok"
    settled = lifecycle.handle_telegram_reply(f"wd:{deploy['token']}:dev", chat_id=99)
    assert settled["action"] == "deploy_settled"
    assert settled.get("token")
    return settled


def test_ticket_lifecycle_is_durable_and_idempotent(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    first = lifecycle.record_ticket_created(
        ticket_id=41, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Client group",
        message_ids=[10, 11],
    )
    second = lifecycle.record_ticket_created(
        ticket_id=41, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Client group",
        message_ids=[10, 11, 12],
    )
    assert first["status"] == "ticket_created"
    assert second["message_ids"] == "[10, 11, 12]"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM whatsapp_work_lifecycles")).scalar_one() == 1


def test_completed_ticket_gates_draft_until_deploy_settled(monkeypatch, tmp_path):
    _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=42, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya",
        message_ids=[20],
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.get.return_value = SimpleNamespace(title="Fix the checkout button")
    monkeypatch.setattr(lifecycle, "get_session", lambda: session)
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_clear_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: kwargs,
    )

    # Before deploy settle, prepare_completed_reply must refuse the client draft.
    assert lifecycle.prepare_completed_reply(
        ticket_id=42, run_id=8, status="completed", result_summary="ok",
    ) is None

    settled = _settle_to_draft(monkeypatch, 42)
    assert "ready for you to look" in settled["draft"].lower() or "checkout" in settled["draft"].lower()
    with lifecycle.engine.connect() as conn:
        row = conn.execute(text(
            "SELECT status, reply_status, deploy_status, deploy_target "
            "FROM whatsapp_work_lifecycles WHERE ticket_id=42"
        )).mappings().one()
    assert row["status"] == "awaiting_reply_review"
    assert row["reply_status"] == "pending"
    assert row["deploy_status"] == "settled"
    assert row["deploy_target"] == "development"


def test_negative_verification_records_lesson_and_blocks_draft(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=46, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya",
        message_ids=[33],
    )
    lessons = []
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_clear_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lifecycle,
        "record_verification_lesson",
        lambda **kwargs: lessons.append(kwargs) or {"recorded": True, "lesson": "do better"},
    )
    gate = lifecycle.begin_post_completion_gates(
        ticket_id=46, run_id=9, status="completed", result_summary="Thin summary",
    )
    rejected = lifecycle.handle_telegram_reply("this is terrible, and this is why: checkout is broken", chat_id=99)
    assert rejected["action"] == "verification_redo"
    assert lessons and "this is terrible" in lessons[0]["feedback"]
    with engine.connect() as conn:
        status = conn.execute(text(
            "SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=46"
        )).scalar_one()
        reply_count = conn.execute(text("SELECT COUNT(*) FROM whatsapp_reply_reviews")).scalar_one()
    assert status == "verification_rejected"
    assert reply_count == 0


def test_telegram_review_revise_then_leave_draft(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=43, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya", message_ids=[30],
    )
    now = 1.0
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=43")).scalar_one()
        conn.execute(text(
            "UPDATE whatsapp_work_lifecycles SET reply_draft='Old draft', "
            "deploy_status='settled', deploy_target='development' WHERE id=:id"
        ), {"id": lifecycle_id})
        conn.execute(text(
            "INSERT INTO whatsapp_reply_reviews(token,lifecycle_id,status,created_at,updated_at) "
            "VALUES ('tok',:id,'pending',:now,:now)"
        ), {"id": lifecycle_id, "now": now})
    saved = {}
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.save_compose_draft",
        lambda **kwargs: saved.update(kwargs) or kwargs,
    )
    prompt = lifecycle.handle_telegram_reply("wa:tok:revise", chat_id=99)
    assert "revised wording" in prompt["text"]
    revised = lifecycle.handle_telegram_reply("Hi Maya, this is ready now.", chat_id=99)
    assert revised["reply_markup"] == lifecycle.review_markup("tok")
    assert saved["text"] == "Hi Maya, this is ready now."
    left = lifecycle.handle_telegram_reply("wa:tok:leave", chat_id=99)
    assert "left the reply" in left["text"]
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=43")).scalar_one()
    assert status == "reply_draft_ready"


def test_reply_controls_never_offer_complete():
    labels = [button["text"] for button in lifecycle.review_markup("abc")["inline_keyboard"][0]]
    assert labels == ["Send", "Revise", "Leave draft"]
    assert "Complete" not in labels


def test_send_review_is_claimed_once_and_failed_send_returns_to_pending(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=44, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya", message_ids=[31],
    )
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=44")).scalar_one()
        conn.execute(text(
            "UPDATE whatsapp_work_lifecycles SET reply_draft='Ready', "
            "deploy_status='settled', deploy_target='production' WHERE id=:id"
        ), {"id": lifecycle_id})
        conn.execute(text(
            "INSERT INTO whatsapp_reply_reviews(token,lifecycle_id,status,created_at,updated_at) "
            "VALUES ('sendtok',:id,'pending',1,1)"
        ), {"id": lifecycle_id})
    calls = []
    monkeypatch.setattr(
        "distr.core.integrations.whatsapp.relay_client.send_message_via_relay",
        lambda **kwargs: calls.append(kwargs) or {"success": False, "error": "offline"},
    )
    failed = lifecycle.handle_telegram_reply("wa:sendtok:send", chat_id=99)
    assert "draft is still saved" in failed["text"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status FROM whatsapp_reply_reviews WHERE token='sendtok'")).scalar_one() == "pending"
    assert len(calls) == 1

    with engine.begin() as conn:
        conn.execute(text("UPDATE whatsapp_reply_reviews SET status='resolving' WHERE token='sendtok'"))
    duplicate = lifecycle.handle_telegram_reply("wa:sendtok:send", chat_id=99)
    assert "already being applied" in duplicate["text"]
    assert len(calls) == 1


def test_send_review_dry_run_skips_live_relay(monkeypatch, tmp_path):
    """Claim/review Send succeeds without a live relay POST when dry-run is on."""
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=45, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya", message_ids=[32],
    )
    with engine.begin() as conn:
        lifecycle_id = conn.execute(text("SELECT id FROM whatsapp_work_lifecycles WHERE ticket_id=45")).scalar_one()
        conn.execute(
            text(
                "UPDATE whatsapp_work_lifecycles SET reply_draft='Ready for customer', "
                "deploy_status='settled', deploy_target='development' WHERE id=:id"
            ),
            {"id": lifecycle_id},
        )
        conn.execute(
            text(
                "INSERT INTO whatsapp_reply_reviews(token,lifecycle_id,status,created_at,updated_at) "
                "VALUES ('drytok',:id,'pending',1,1)"
            ),
            {"id": lifecycle_id},
        )

    monkeypatch.setenv("DECISIONSAI_WHATSAPP_DRY_RUN", "1")
    posts = []

    import distr.core.integrations.whatsapp.relay_client as relay

    monkeypatch.setattr(
        relay.requests,
        "post",
        lambda *a, **k: posts.append((a, k)) or (_ for _ in ()).throw(
            AssertionError("live relay POST must not run in dry-run")
        ),
    )
    monkeypatch.setattr(
        "distr.core.kanban.whatsapp_compose_drafts.delete_compose_draft",
        lambda *_a, **_k: None,
    )

    result = lifecycle.handle_telegram_reply("wa:drytok:send", chat_id=99)
    assert result["handled"] is True
    assert "sent" in result["text"].lower()
    assert posts == []
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT status FROM whatsapp_reply_reviews WHERE token='drytok'")
        ).scalar_one() == "sent"
        assert conn.execute(
            text("SELECT status FROM whatsapp_work_lifecycles WHERE ticket_id=45")
        ).scalar_one() == "reply_sent"


def test_notify_phrase_asks_send_to_customer(monkeypatch):
    review = {"token": "abc", "contact": "Maya", "draft": "Hi Maya", "deploy_target": "development"}
    captured = {}

    class FakeManager:
        def send_to_telegram(self, message, reply_markup=None):
            captured["text"] = message
            captured["reply_markup"] = reply_markup
            return True

    monkeypatch.setattr(
        "distr.core.kanban.ticket_workflow_engagement._telegram_manager_from_app",
        lambda: FakeManager(),
    )
    assert lifecycle.notify_telegram_review(review) is True
    assert "send to customer" in captured["text"].lower()
    assert captured["reply_markup"] == lifecycle.review_markup("abc")


def test_suggest_deploy_target_prefers_production_for_customer_fix():
    assert lifecycle.suggest_deploy_target(
        result_summary="Hotfix shipped to production checkout",
        ticket_title="Fix checkout",
        contact="Maya",
    ) == "production"
    assert lifecycle.suggest_deploy_target(
        result_summary="Spike experiment on staging",
        ticket_title="Explore",
        contact="",
    ) == "development"


def test_is_negative_verification_feedback_phrases():
    assert lifecycle.is_negative_verification_feedback("this is terrible, and this is why")
    assert lifecycle.is_negative_verification_feedback("redo")
    assert not lifecycle.is_negative_verification_feedback("looks good")
    assert not lifecycle.is_negative_verification_feedback("ok")
    assert not lifecycle.is_negative_verification_feedback("wrong")


@patch("distr.core.workflow.skill_judgment_memory.record_skill_judgment_event")
@patch("distr.core.workflow.skill_judgment_memory.resolve_judgment_skill_context")
@patch("distr.core.orchestrator.record_learning_signal")
@patch("distr.core.workflow.steering_memory.record_run_steering_feedback")
def test_record_verification_lesson_wires_steering_and_learned_rule(
    mock_steer, mock_signal, mock_resolve, mock_event,
):
    mock_resolve.return_value = {
        "skill_id": "webapp-testing",
        "skill_name": "Webapp Testing",
        "skill_ids": ["webapp-testing"],
        "harness_category": "frontend",
        "execution_lane": "workflow",
    }
    mock_event.return_value = {
        "event_id": 55,
        "proposal": {"proposal_id": 9, "status": "pending", "applied": False},
    }
    out = lifecycle.record_verification_lesson(
        run_id=12,
        board_id=7,
        project_id=3,
        ticket_id=46,
        feedback="this is terrible, and this is why: checkout is broken",
        ticket_title="Fix checkout",
        result_summary="Thin QA",
        skill_id="webapp-testing",
        harness_category="frontend",
    )
    assert out["recorded"] is True
    assert out["skill_id"] == "webapp-testing"
    assert out["harness_category"] == "frontend"
    assert out["proposal"] and out["proposal"]["status"] == "pending"
    assert "verification rejected" in out["lesson"].lower() or "fix checkout" in out["lesson"].lower()
    mock_steer.assert_called_once()
    assert mock_steer.call_args.kwargs["event_type"] == "changes_requested"
    assert mock_steer.call_args.kwargs["rule_type"] == "verification_lesson"
    mock_signal.assert_called_once()
    assert mock_signal.call_args.kwargs["enabled"] is True
    assert mock_signal.call_args.kwargs["rule_type"] == "verification_lesson"
    assert mock_signal.call_args.kwargs["payload"]["skill_id"] == "webapp-testing"
    mock_event.assert_called_once()


def test_begin_post_completion_stores_skill_context(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=50, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya",
        message_ids=[50],
    )
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "notify_telegram_verification", lambda *_a, **_k: True)
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.get.return_value = SimpleNamespace(title="Fix react checkout CSS")
    monkeypatch.setattr(lifecycle, "get_session", lambda: session)

    class _Run:
        run_data = '{"turn_skill_ids": ["frontend-design"], "execution_lane": "workflow"}'

    class _DB:
        def query(self, *_a, **_k):
            return self
        def filter(self, *_a, **_k):
            return self
        def first(self):
            return _Run()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    # resolve_judgment_skill_context uses get_session from skill_judgment_memory
    import distr.core.workflow.skill_judgment_memory as sjm
    monkeypatch.setattr(sjm, "engine", engine)
    sjm.ensure_tables()
    monkeypatch.setattr(sjm, "get_session", lambda: _DB())
    monkeypatch.setattr(lifecycle, "verification_lessons_context", lambda **kwargs: "")

    gate = lifecycle.begin_post_completion_gates(
        ticket_id=50, run_id=22, status="completed", result_summary="Styled",
    )
    assert gate and gate["gate_kind"] == "verification"
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT skill_id, skill_ids_json, execution_lane FROM whatsapp_work_lifecycles WHERE ticket_id=50"
        )).mappings().one()
    assert row["skill_id"] == "frontend-design"
    assert "frontend-design" in (row["skill_ids_json"] or "")
    assert row["execution_lane"] == "workflow"


def test_negative_verification_passes_skill_into_lesson(monkeypatch, tmp_path):
    engine = _isolated_engine(monkeypatch, tmp_path)
    lifecycle.record_ticket_created(
        ticket_id=51, board_id=7, project_id=3,
        source_jid="120@g.us", source_phone="120", source_contact="Maya",
        message_ids=[51],
    )
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE whatsapp_work_lifecycles SET skill_id='frontend-design', "
            "skill_name='Frontend Design', skill_ids_json='[\"frontend-design\"]', "
            "harness_category='frontend', execution_lane='workflow' WHERE ticket_id=51"
        ))
    lessons = []
    monkeypatch.setattr(lifecycle, "_set_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(lifecycle, "_clear_run_waiting_kind", lambda *_a, **_k: None)
    monkeypatch.setattr(
        lifecycle,
        "record_verification_lesson",
        lambda **kwargs: lessons.append(kwargs) or {"recorded": True, "lesson": "do better"},
    )
    monkeypatch.setattr(lifecycle, "verification_lessons_context", lambda **kwargs: "")
    gate = lifecycle.begin_post_completion_gates(
        ticket_id=51, run_id=23, status="completed", result_summary="Thin",
    )
    # Re-stamp skill columns in case begin overwrote from empty run resolve
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE whatsapp_work_lifecycles SET skill_id='frontend-design', "
            "skill_ids_json='[\"frontend-design\"]', harness_category='frontend' WHERE ticket_id=51"
        ))
    rejected = lifecycle.handle_telegram_reply("redo", chat_id=99)
    assert rejected["action"] == "verification_redo"
    assert lessons
    assert lessons[0].get("skill_id") == "frontend-design"
    assert lessons[0].get("harness_category") == "frontend"
