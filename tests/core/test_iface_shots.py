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


def test_production_url_comes_from_a_hostname_folder():
    from distr.core.planning.iface_shots import production_url
    assert production_url("/tmp/www.merrypak.co.za") == "https://www.merrypak.co.za"
    assert production_url("/tmp/DecisionsAI") == ""
    assert production_url("/tmp/app", "open https://shop.example.com") == "https://shop.example.com"


def test_capture_plan_falls_back_to_production_when_terminals_are_down():
    from distr.core.planning.iface_shots import capture_plan, describe_targets
    targets = describe_targets(False, [], "", production="https://www.merrypak.co.za")
    assert targets["production_url"] == "https://www.merrypak.co.za"
    assert targets["reason"] == ""
    grab = capture_plan(targets, lane="frontend", route="/shop", saved=False)
    assert grab == {"action": "grab", "url": "https://www.merrypak.co.za/shop", "reason": ""}
    assert capture_plan(targets, lane="frontend", route="/shop", saved=True)["action"] == "read"
    assert capture_plan(targets, lane="frontend", route="/shop", saved=True, stale=True)["action"] == "read"
    assert capture_plan(targets, lane="frontend", route="/shop", saved=True, force=True)["action"] == "grab"


def test_clean_url_allows_only_the_project_production_host():
    from distr.core.planning.iface_shots import _clean_url
    allowed = {"www.merrypak.co.za"}
    assert _clean_url("https://www.merrypak.co.za/login", allowed).startswith("https://www.merrypak.co.za")
    assert _clean_url("https://evil.example/login", allowed) == ""
    assert _clean_url("http://127.0.0.1:5173/shop", allowed).startswith("http://127.0.0.1")


def test_clear_shot_files_removes_screenshots_only(tmp_path):
    from distr.core.planning.iface_shots import _clear_shot_files
    (tmp_path / "checkout.png").write_bytes(b"png")
    (tmp_path / "checkout.json").write_text('{"captured_at": "2026-11-13T00:00:00+00:00", "screen_id": "checkout"}\n')
    (tmp_path / "iface-layout.json").write_text('{"nodes": {}, "v": 1}\n')
    (tmp_path / "27-flows-and-wireframes.wire").write_text("screen \"Home\"\n")
    (tmp_path / "orphan.json").write_text('{"captured_at": "2026-11-13T00:00:00+00:00", "screen_id": "orphan"}\n')
    cleared = _clear_shot_files(tmp_path)
    assert cleared >= 2
    assert not (tmp_path / "checkout.png").exists()
    assert not (tmp_path / "checkout.json").exists()
    assert (tmp_path / "iface-layout.json").exists()
    assert (tmp_path / "27-flows-and-wireframes.wire").exists()
