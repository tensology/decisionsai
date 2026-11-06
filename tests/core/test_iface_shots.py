"""Interface screenshot decisions. No browser is launched."""
from distr.core.planning.iface_shots import (
    NO_WEB,
    TERMINALS_DOWN,
    capture_plan,
    concrete_route,
    describe_targets,
)


def test_terminals_down_has_no_url():
    targets = describe_targets(False, [], "npm run dev http://127.0.0.1:5173")
    assert targets["terminals_up"] is False
    assert targets["frontend_url"] == ""
    assert targets["backend_url"] == ""
    assert targets["reason"] == TERMINALS_DOWN


def test_live_frontend_and_backend_urls_come_from_the_terminals():
    sessions = [
        {"command": "npm run dev", "urls": [{"url": "http://localhost:5173"}]},
        {"command": "python manage.py runserver", "urls": [{"url": "http://127.0.0.1:8000"}]},
    ]
    targets = describe_targets(True, sessions, "")
    assert targets["frontend_url"] == "http://localhost:5173"
    assert targets["backend_url"] == "http://127.0.0.1:8000"
    assert targets["reason"] == ""


def test_startup_instruction_url_is_used_only_while_terminals_are_up():
    targets = describe_targets(True, [{"command": "npm run dev", "urls": []}], "npm run dev http://127.0.0.1:5173")
    assert targets["frontend_url"] == "http://127.0.0.1:5173"
    assert targets["backend_url"] == ""


def test_python_without_a_url_is_not_given_one():
    targets = describe_targets(True, [{"command": "python tool.py", "urls": []}], "python tool.py")
    assert targets["frontend_url"] == ""
    assert targets["backend_url"] == ""
    assert targets["reason"] == NO_WEB


def test_concrete_route_rejects_a_pattern_or_a_host():
    assert concrete_route("/shop") == "/shop"
    assert concrete_route("/orders/:id") == ""
    assert concrete_route("https://example.com") == ""


def test_capture_plan_skips_a_down_project_and_reads_a_saved_file():
    down = describe_targets(False, [], "")
    assert capture_plan(down, lane="frontend", route="/shop", saved=False)["action"] == "skip"
    assert capture_plan(down, lane="frontend", route="/shop", saved=True)["action"] == "read"
    up = describe_targets(True, [{"command": "npm run dev", "urls": [{"url": "http://127.0.0.1:5173"}]}], "")
    grab = capture_plan(up, lane="frontend", route="/shop", saved=False)
    assert grab == {"action": "grab", "url": "http://127.0.0.1:5173/shop", "reason": ""}
    assert capture_plan(up, lane="ajax", route="/api/orders", saved=False)["action"] == "skip"
