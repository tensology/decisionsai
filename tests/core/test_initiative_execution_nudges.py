from datetime import datetime, timedelta
from types import SimpleNamespace


class DummyTelegram:
    telegram_user_id = 12345

    def __init__(self):
        self.sent = []

    def send_to_telegram(self, text):
        self.sent.append(text)


class DummyEventQueue:
    def __init__(self):
        self.items = []

    def put(self, item, block=False):
        self.items.append((item, block))


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def outerjoin(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, project_rows, workflow_rows):
        self.project_rows = project_rows
        self.workflow_rows = workflow_rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query(self, *models):
        first_name = getattr(models[0], "__name__", "")
        if first_name == "ProjectExecutionSession":
            return FakeQuery(self.project_rows)
        return FakeQuery(self.workflow_rows)


def test_initiative_sends_one_human_stale_ide_session_nudge_without_placeholder_name(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    row = SimpleNamespace(
        id=7001,
        project_id=42,
        route_type="ide_bridge",
        route_backend="cursor",
        status="running",
        started_at=datetime.utcnow() - timedelta(minutes=16),
        updated_at=datetime.utcnow() - timedelta(minutes=16),
        completed_at=None,
    )
    project = SimpleNamespace(
        id=42,
        name="Quiet App",
        folder_location="/tmp/customer-portal",
        coding_backend="cursor",
    )
    fake_session = FakeSession(project_rows=[(row, project)], workflow_rows=[])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 1800
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})
    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert len(manager.sent) == 1
    assert "Quiet App" not in manager.sent[0]
    assert "Cursor" in manager.sent[0]
    assert "customer-portal" in manager.sent[0]
    assert "looks idle" not in manager.sent[0]
    assert "I will not keep reminding you" not in manager.sent[0]
    assert "Ask me to check it" in manager.sent[0]


def test_initiative_does_not_repeat_idle_nudge_when_only_updated_at_changes(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    started = datetime.utcnow() - timedelta(hours=2)
    row = SimpleNamespace(
        id=7101,
        project_id=43,
        route_type="ide_bridge",
        route_backend="codex",
        status="running",
        started_at=started,
        updated_at=datetime.utcnow() - timedelta(minutes=16),
        completed_at=None,
        input_packet='{"instruction": "tighten the automation web UI", "source": "codex"}',
        output_packet=None,
    )
    project = SimpleNamespace(
        id=43,
        name="Decisions",
        folder_location="/tmp/decisions",
        coding_backend="codex",
    )
    fake_session = FakeSession(project_rows=[(row, project)], workflow_rows=[])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 0
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})
    row.updated_at = datetime.utcnow() - timedelta(minutes=18)
    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert len(manager.sent) == 1
    assert "tighten the automation web UI" in manager.sent[0]


def test_initiative_skips_ide_idle_nudge_after_twenty_minutes(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    row = SimpleNamespace(
        id=7151,
        project_id=43,
        route_type="ide_bridge",
        route_backend="cursor",
        status="running",
        started_at=datetime.utcnow() - timedelta(minutes=35),
        updated_at=datetime.utcnow() - timedelta(minutes=35),
        completed_at=None,
        input_packet='{"instruction": "review the web UI", "source": "cursor"}',
        output_packet=None,
    )
    project = SimpleNamespace(
        id=43,
        name="Cursor Project",
        folder_location="/tmp/decisions",
        coding_backend="cursor",
    )
    fake_session = FakeSession(project_rows=[(row, project)], workflow_rows=[])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 1800
    service._execution_terminal_notice_window_s = 3600
    service._execution_idle_max_notice_age_s = 1200

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert manager.sent == []


def test_initiative_uses_neutral_label_for_ambiguous_ide_backend(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    row = SimpleNamespace(
        id=7201,
        project_id=44,
        route_type="ide_bridge",
        route_backend="",
        status="running",
        started_at=datetime.utcnow() - timedelta(minutes=16),
        updated_at=datetime.utcnow() - timedelta(minutes=16),
        completed_at=None,
        input_packet="{}",
        output_packet=None,
    )
    project = SimpleNamespace(
        id=44,
        name="Cursor Project",
        folder_location="/tmp/decisions",
        coding_backend="",
    )
    fake_session = FakeSession(project_rows=[(row, project)], workflow_rows=[])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 1800
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert len(manager.sent) == 1
    assert "Cursor on Cursor Project" not in manager.sent[0]
    assert "the IDE session" in manager.sent[0]
    assert "decisions" in manager.sent[0]


def test_initiative_prefers_packet_source_over_stale_backend_label(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    row = SimpleNamespace(
        id=7301,
        project_id=45,
        route_type="ide_bridge",
        route_backend="cursor",
        status="running",
        started_at=datetime.utcnow() - timedelta(minutes=16),
        updated_at=datetime.utcnow() - timedelta(minutes=16),
        completed_at=None,
        input_packet='{"source": "codex", "instruction": "wire the IDE bridge"}',
        output_packet=None,
    )
    project = SimpleNamespace(
        id=45,
        name="Decisions",
        folder_location="/tmp/decisions",
        coding_backend="cursor",
    )
    fake_session = FakeSession(project_rows=[(row, project)], workflow_rows=[])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 1800
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert len(manager.sent) == 1
    assert "Codex" in manager.sent[0]
    assert "Cursor" not in manager.sent[0]
    assert "wire the IDE bridge" in manager.sent[0]


def test_initiative_does_not_repeat_workflow_stale_nudge(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    run = SimpleNamespace(
        id=7401,
        workflow_id=55,
        status="running",
        started_at=datetime.utcnow() - timedelta(minutes=16),
        completed_at=None,
    )
    workflow = SimpleNamespace(id=55, name="Screen Compliment")
    fake_session = FakeSession(project_rows=[], workflow_rows=[(run, workflow)])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 0
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})
    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert len(manager.sent) == 1
    assert "Screen Compliment" in manager.sent[0]
    assert "I'll leave it alone" not in manager.sent[0]
    assert "I will not keep reminding you" not in manager.sent[0]
    assert "I can inspect it if you ask" in manager.sent[0]


def test_initiative_skips_orphaned_ide_bridge_fixture_rows(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    row = SimpleNamespace(
        id=7451,
        project_id=9999,
        route_type="ide_bridge",
        route_backend="codex",
        status="running",
        started_at=datetime.utcnow() - timedelta(minutes=16),
        updated_at=datetime.utcnow() - timedelta(minutes=16),
        completed_at=None,
        input_packet='{"project_name": "Demo IDE", "folder": "/private/var/folders/pytest-of-paul/pytest-999/demo"}',
        output_packet=None,
    )
    fake_session = FakeSession(project_rows=[(row, None)], workflow_rows=[])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 0
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert manager.sent == []


def test_initiative_skips_automation_run_status_rows(monkeypatch):
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    run = SimpleNamespace(
        id=7461,
        workflow_id=66,
        status="completed",
        started_at=datetime.utcnow() - timedelta(minutes=1),
        completed_at=datetime.utcnow(),
    )
    workflow = SimpleNamespace(
        id=66,
        name="Screen Compliment",
        workflow_type="scheduled",
        context_rules='{"decisions_surface": "automation"}',
    )
    fake_session = FakeSession(project_rows=[], workflow_rows=[(run, workflow)])
    monkeypatch.setattr("distr.core.db.get_session", lambda: fake_session)

    manager = DummyTelegram()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service._execution_notice_cache = {}
    service._execution_stale_after_s = 900
    service._execution_stale_repeat_s = 0
    service._execution_terminal_notice_window_s = 3600

    service._maybe_send_execution_nudges({"initiative_allow_telegram": True})

    assert manager.sent == []


def test_initiative_idle_nudge_uses_text_even_when_event_queue_is_available():
    from distr.core.human_engagement import reset_engagement_ledger
    from distr.core.initiative.service import InitiativeService
    from distr.core.notification_routing import reset_notification_activity

    reset_notification_activity()
    reset_engagement_ledger()

    manager = DummyTelegram()
    event_queue = DummyEventQueue()
    service = InitiativeService.__new__(InitiativeService)
    service.telegram_manager = manager
    service.event_queue = event_queue

    service._send_telegram_if_allowed(
        "Cursor looks idle.",
        {"initiative_allow_telegram": True},
        kind="idle_nudge",
        subject_type="ide_session",
        subject_id="cursor-voice",
        state_fingerprint="quiet",
        requires_response=True,
    )

    assert manager.sent == ["Cursor looks idle."]
    assert event_queue.items == []
