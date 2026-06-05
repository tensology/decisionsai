import time
from pathlib import Path


class DummyTelegram:
    telegram_user_id = 12345

    def __init__(self, connected=True):
        self.connected = connected

    def is_connected(self):
        return self.connected


def test_voice_first_telegram_decision_when_user_has_not_asked_for_text():
    from distr.core.human_engagement import (
        EngagementIntent,
        HumanEngagementService,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import record_surface_activity, reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    record_surface_activity("telegram", at=100)

    service = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 120)
    decision = service.decide(EngagementIntent(
        source="initiative",
        surface="telegram",
        kind="idle_nudge",
        priority="normal",
        subject_type="ide_session",
        subject_id="cursor-1",
        state_fingerprint="unchanged",
        body="Cursor looks idle.",
        voice_body="Cursor looks idle.",
        requires_response=True,
    ))

    assert decision.should_send is True
    assert decision.channel == "telegram"
    assert decision.format == "voice"
    assert decision.final_text is None
    assert decision.final_voice_text == "Cursor looks idle."


def test_same_unanswered_prompt_is_suppressed_until_state_changes():
    from distr.core.human_engagement import (
        EngagementIntent,
        HumanEngagementService,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    service = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 120)

    intent = EngagementIntent(
        source="initiative",
        surface="telegram",
        kind="idle_nudge",
        priority="normal",
        subject_type="ide_session",
        subject_id="cursor-1",
        state_fingerprint="quiet-for-15m",
        body="Cursor looks idle.",
        requires_response=True,
    )

    first = service.decide(intent)
    second = service.decide(intent)
    changed = service.decide(intent.with_state("quiet-for-activity-changed"))

    assert first.should_send is True
    assert second.should_send is False
    assert second.suppress_reason == "awaiting_user_response"
    assert changed.should_send is True


def test_low_value_status_update_is_silent_even_without_response_requirement():
    from distr.core.human_engagement import (
        EngagementIntent,
        HumanEngagementService,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    service = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 120)
    intent = EngagementIntent(
        source="app_events",
        surface="telegram",
        kind="status_update",
        priority="low",
        subject_type="status",
        subject_id="telegram",
        state_fingerprint="screen-compliment-saved",
        body="Screen Compliment saved successfully - I saved it in Decisions.",
        requires_response=False,
    )

    decision = service.decide(intent)

    assert decision.should_send is False
    assert decision.suppress_reason == "low_value_status"


def test_terminal_execution_status_is_deduped_without_response_requirement():
    from distr.core.human_engagement import (
        EngagementIntent,
        HumanEngagementService,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    service = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 120)
    intent = EngagementIntent(
        source="initiative",
        surface="telegram",
        kind="execution_terminal",
        priority="normal",
        subject_type="ide_session",
        subject_id="codex-42",
        state_fingerprint="completed",
        body="Codex finished DecisionsAI. Result: complete.",
    )

    first = service.decide(intent)
    second = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 180).decide(intent)

    assert first.should_send is True
    assert second.should_send is False
    assert second.suppress_reason == "duplicate_state"


def test_low_value_status_can_send_only_with_explicit_notification_intent():
    from distr.core.human_engagement import (
        EngagementIntent,
        HumanEngagementService,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    service = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 120)

    decision = service.decide(EngagementIntent(
        source="app_events",
        surface="telegram",
        kind="status_update",
        priority="normal",
        subject_type="status",
        subject_id="telegram",
        state_fingerprint="screen-compliment-saved",
        body="Screen Compliment saved successfully - I saved it in Decisions.",
        explicit_notification_intent=True,
        allow_voice=False,
    ))

    assert decision.should_send is True
    assert decision.format == "text"


def test_sanitized_copy_removes_markdown_links_and_raw_provider_errors():
    from distr.core.human_engagement import sanitize_engagement_text

    clean = sanitize_engagement_text(
        "Workflow Screen Compliment [failed]: Error: You exceeded your current quota. "
        "https://platform.openai.com/docs/guides/error-codes/api-errors\n"
        "## Details\n- raw stack trace"
    )

    assert clean == "Screen Compliment failed. I've logged the details in Decisions."
    assert "https://" not in clean
    assert "##" not in clean
    assert "quota" not in clean.lower()


def test_remote_control_link_is_preserved_and_sent_as_text():
    from distr.core.human_engagement import (
        EngagementIntent,
        HumanEngagementService,
        sanitize_engagement_text,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    text = "Remote Control:\nhttps://www.decisionsai.net/api/remote/?channel=abc123\nDo not share this link."

    assert "https://www.decisionsai.net/api/remote/" in sanitize_engagement_text(text, preserve_links=True)

    decision = HumanEngagementService(telegram_manager=DummyTelegram(), now=lambda: 120).decide(EngagementIntent(
        source="telegram",
        surface="telegram",
        kind="remote_link",
        priority="normal",
        subject_type="remote",
        subject_id="telegram",
        state_fingerprint="abc123",
        body=text,
        voice_body=text,
        explicit_notification_intent=True,
    ))

    assert decision.should_send is True
    assert decision.format == "text"
    assert decision.final_text and "https://www.decisionsai.net/api/remote/" in decision.final_text


def test_placeholder_project_labels_use_workspace_or_neutral_label():
    from distr.core.human_engagement import human_project_label

    assert human_project_label("Quiet App", workspace_path="/tmp/acme-dashboard", surface="cursor") == "acme-dashboard"
    assert human_project_label("Cursor Project", workspace_path="", surface="cursor") == "the Cursor session"


def test_attachments_are_dropped_without_explicit_artifact_intent(tmp_path):
    from distr.core.human_engagement import (
        EngagementAttachment,
        EngagementIntent,
        HumanEngagementService,
        reset_engagement_ledger,
    )
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()
    report = tmp_path / "report.md"
    report.write_text("hello")

    service = HumanEngagementService(telegram_manager=DummyTelegram(), now=time.time)
    decision = service.decide(EngagementIntent(
        source="tool",
        surface="telegram",
        kind="tool_result",
        priority="normal",
        subject_type="tool",
        subject_id="send-file",
        state_fingerprint="report",
        body="Here is the report.",
        attachments=[EngagementAttachment(path=str(report), kind="document", name="report.md")],
        explicit_artifact_intent=False,
    ))

    assert decision.should_send is True
    assert decision.attachments == []
    assert decision.final_text == "Here is the report."


def test_engagement_surface_does_not_hardcode_personal_name():
    root = Path(__file__).resolve().parents[2]
    scanned_roots = [
        root / "distr" / "app",
        root / "distr" / "core",
        root / "tests",
    ]
    offenders = []
    for base in scanned_roots:
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {"models", "__pycache__"} for part in path.parts):
                continue
            if path.suffix not in {".py", ".md", ".txt"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if ("Pa" + "ul") in text:
                offenders.append(str(path.relative_to(root)))

    assert offenders == []
