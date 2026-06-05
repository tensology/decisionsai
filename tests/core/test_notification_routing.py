from distr.core.notification_routing import (
    clear_notification_activity_cache,
    choose_notification_route,
    last_surface_activity,
    record_surface_activity,
    reset_notification_activity,
)


class DummyTelegram:
    telegram_user_id = 12345

    def __init__(self, connected=True):
        self.connected = connected

    def is_connected(self):
        return self.connected


def test_notification_route_uses_most_recent_surface():
    reset_notification_activity()
    manager = DummyTelegram()

    record_surface_activity("desktop", at=100)
    record_surface_activity("telegram", at=120)

    route = choose_notification_route(
        telegram_manager=manager,
        allow_telegram=True,
        now=130,
    )

    assert route.surface == "telegram"
    assert "most recent" in route.reason


def test_notification_route_uses_desktop_without_telegram_permission():
    reset_notification_activity()

    record_surface_activity("desktop", at=100)

    route = choose_notification_route(
        telegram_manager=DummyTelegram(),
        allow_telegram=False,
        now=130,
    )

    assert route.surface == "desktop"


def test_notification_route_uses_remote_pending_context():
    reset_notification_activity()
    manager = DummyTelegram()
    manager._pending_remote_agent_response = {"request_id": "r1", "created_at": 100}

    route = choose_notification_route(
        telegram_manager=manager,
        allow_telegram=True,
        now=120,
    )

    assert route.surface == "remote"
    assert "remote control" in route.reason


def test_notification_route_falls_back_to_telegram_when_idle():
    reset_notification_activity()

    route = choose_notification_route(
        telegram_manager=DummyTelegram(),
        allow_telegram=True,
        now=120,
    )

    assert route.surface == "telegram"
    assert "fallback" in route.reason


def test_notification_route_persists_activity_and_maps_ide_to_desktop():
    reset_notification_activity()

    record_surface_activity("cursor", at=100, metadata={"project_id": 10})
    clear_notification_activity_cache()

    assert last_surface_activity("cursor") == 100
    route = choose_notification_route(
        telegram_manager=DummyTelegram(),
        allow_telegram=True,
        now=130,
    )

    assert route.surface == "desktop"
    assert "cursor" in route.reason


def test_notification_route_accepts_external_activity_without_using_it_as_delivery_surface():
    reset_notification_activity()

    record_surface_activity("whatsapp", at=100)
    route = choose_notification_route(
        telegram_manager=DummyTelegram(),
        allow_telegram=True,
        now=130,
    )

    assert route.surface == "telegram"
    assert last_surface_activity("whatsapp") == 100
