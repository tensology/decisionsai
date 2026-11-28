"""Planning fixes: bundled trees stay out of the scan, captured pages fill empty cues,
failed captures are not retried on every open, and unchanged sources skip the open-time scan."""
import json
import time
from pathlib import Path

from distr.core.planning import iface_shots, project_scan, service


def test_build_output_and_vendored_packs_are_not_scanned(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("x = 1\n")
    release = tmp_path / "installer" / "release" / "pack"
    release.mkdir(parents=True)
    (release / "models.py").write_text("class Cart: pass\n")
    vendored = tmp_path / "plugins" / "shop"
    vendored.mkdir(parents=True)
    (vendored / project_scan._VENDOR_MARKER).write_text("{}")
    (vendored / "models.py").write_text("class Customer: pass\n")
    names = {p.relative_to(tmp_path).as_posix() for p in project_scan._source_files(tmp_path)}
    assert "app/models.py" in names
    assert not any(n.startswith(("installer/release", "plugins/shop")) for n in names)


def test_captured_page_fills_only_an_empty_cue(tmp_path: Path):
    planning = tmp_path / "planning"
    planning.mkdir()
    cue = {"fields": [{"label": "Username"}], "buttons": ["Log in"]}
    (planning / "admin.json").write_text(json.dumps({"url": "https://example.test/admin/", "cues": cue}))
    (planning / "home.json").write_text(json.dumps({"url": "https://example.test/", "cues": cue}))
    source_cue = {"fields": [{"label": "Search"}]}
    context = {"routes": [{"url": "/admin/"}, {"url": "/"}], "template_cues": {"/": source_cue}}
    assert project_scan._apply_captured_cues(tmp_path, context) == 1
    assert context["template_cues"]["/admin/"]["observed"] is True
    assert context["template_cues"]["/admin/"]["fields"] == cue["fields"]
    assert context["template_cues"]["/"] is source_cue


def test_failed_capture_is_remembered_then_forgotten(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(iface_shots, "_ATTEMPT_FILE", tmp_path / "attempts.json")
    assert iface_shots._recent_attempt(4, "admin") is None
    iface_shots._record_attempt(4, "admin", "timeout")
    assert iface_shots._recent_attempt(4, "admin")["reason"] == "timeout"
    monkeypatch.setattr(iface_shots, "_RETRY_SECONDS", 0)
    assert iface_shots._recent_attempt(4, "admin") is None
    iface_shots._record_attempt(4, "admin", None)
    assert json.loads((tmp_path / "attempts.json").read_text()) == {}


def test_browser_env_drops_dyld_paths(monkeypatch):
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", "/opt/homebrew/lib")
    env = iface_shots._browser_env()
    assert not any(key.startswith("DYLD_") for key in env)


def test_open_skips_scan_while_sources_are_unchanged(tmp_path: Path, monkeypatch):
    (tmp_path / "views.py").write_text("x = 1\n")
    monkeypatch.setattr(service, "_SCAN_STAMP_DIR", tmp_path / "stamps")
    monkeypatch.setattr(service, "_project_root_for", lambda project_id: tmp_path.resolve())
    assert service._scan_is_current(9, 1) is False
    service._write_scan_stamp(9, 1)
    assert service._scan_is_current(9, 1) is True
    time.sleep(0.01)
    (tmp_path / "views.py").write_text("x = 22\n")
    assert service._scan_is_current(9, 1) is False


def test_unwired_templates_do_not_make_the_page_map_partial(tmp_path: Path):
    root = tmp_path / "shop"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "home.html").write_text("<h1>Home</h1>")
    (root / "templates" / "admin" ).mkdir()
    (root / "templates" / "admin" / "change_list.html").write_text("<div>admin</div>")
    (root / "urls.py").write_text(
        "from django.urls import path\nfrom . import views\n"
        "urlpatterns = [path('', views.home, name='home')]\n"
    )
    (root / "views.py").write_text("def home(request):\n    return render(request, 'home.html')\n")
    model = project_scan.build_html_sitemap(root)
    pages = [node for node in model["nodes"] if node["kind"] == "page"]
    assert pages and all(node["status"] in {"verified", "inferred"} for node in pages)
    assert model["coverage"] in {"verified", "inferred"}
    assert not any(node.get("template") == "admin/change_list.html" for node in model["nodes"])
    assert any(item["template"] == "admin/change_list.html" for item in model["scaffolding"])


def test_redirects_and_robots_are_endpoints_not_missing_pages(tmp_path: Path):
    root = tmp_path / "shop"
    (root / "templates").mkdir(parents=True)
    (root / "templates" / "home.html").write_text("<h1>Home</h1>")
    (root / "urls.py").write_text(
        "from django.urls import path\nfrom django.views.generic import RedirectView\nfrom . import views\n"
        "urlpatterns = ["
        " path('', views.home, name='home'),"
        " path('old/', RedirectView.as_view(url='/'), name='old'),"
        " path('robots.txt', views.robots_txt, name='robots'),"
        "]\n"
    )
    (root / "views.py").write_text(
        "def home(request):\n    return render(request, 'home.html')\n"
        "def robots_txt(request):\n    return HttpResponse('User-agent: *')\n"
    )
    model = project_scan.build_html_sitemap(root)
    routes = {node["route"] for node in model["nodes"] if node.get("kind") == "page"}
    endpoint_routes = {row["route"] for row in model["endpoints"]}
    assert "/" in routes
    assert "/old/" in endpoint_routes or "/old" in endpoint_routes
    assert any("robots" in row["route"] for row in model["endpoints"])
    assert not any("robots" in (node.get("route") or "") for node in model["nodes"])


def test_project_file_assets_lists_csv_and_images_but_not_plan_shots(tmp_path: Path):
    from distr.core.planning.blueprint import project_file_assets
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "prices.csv").write_text("sku,price\n1,2\n")
    (tmp_path / "planning").mkdir()
    (tmp_path / "planning" / "admin.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assets = project_file_assets(tmp_path)
    paths = {row["path"] for row in assets}
    assert "media/hero.png" in paths
    assert "data/prices.csv" in paths
    assert "planning/admin.png" not in paths



def test_wire_item_parser_strips_attributes_and_icon_tokens():
    from distr.core.planning.blueprint import parse_wire_item, _wire_cues

    assert parse_wire_item('input "password" type=password') == ("password", {"type": "password"})
    assert parse_wire_item('button "Log in arrow_forward" variant=primary') == ("Log in", {"variant": "primary"})
    assert parse_wire_item('link "arrow_back Return to site" route="/"') == ("Return to site", {"route": "/"})
    cues = _wire_cues([{
        "content_format": "wire",
        "content": """
screen "/admin/" device=desktop route="/admin"
  stack
    heading "/admin/"
    form "/admin/"
      input "username"
      input "password" type=password
      button "Log in arrow_forward" variant=primary
    link "arrow_back Return to site" route="/"
""",
    }])
    admin = cues["/admin"]
    assert admin["fields"] == [{"name": "username"}, {"name": "password", "type": "password"}]
    assert admin["buttons"] == ["Log in"]
    assert admin["links"] == [{"label": "Return to site", "route": "/"}]
    assert "/admin/" not in admin["sections"]


def test_page_ticket_draft_uses_access_and_ready_reason():
    from distr.core.planning import blueprint as bp

    page = {
        "label": "Checkout",
        "route": "/shop/checkout",
        "access": "public",
        "status": "wired",
        "template": "",
        "view": "",
        "fields": [],
        "sections": [],
        "buttons": [],
        "links": [],
        "shot": {"name": "checkout.png", "captured_at": "2026-11-13T18:34:11.584Z"},
    }
    admin = {
        "label": "/admin",
        "route": "/admin",
        "access": "signed-in",
        "status": "wired",
        "template": "",
        "view": "",
        "fields": [{"name": "username"}, {"name": "password", "type": "password"}],
        "sections": [],
        "buttons": ["Log in"],
        "links": [{"label": "Return to site", "route": "/"}],
        "shot": {"name": "admin.png", "captured_at": "2026-11-28T07:22:53Z"},
    }

    def fake_blueprint(workspace_id):
        return {
            "board": "Merrypak",
            "pages": [page, admin],
            "erd": {"tables": [{"name": "BasketItem"}], "relations": []},
        }

    original = bp.plan_blueprint
    bp.plan_blueprint = fake_blueprint
    try:
        checkout = bp.page_ticket_draft(4, "/shop/checkout")
        assert checkout["ready"] is False
        assert "stencil" in checkout["ready_reason"]
        assert "a visitor" in checkout["description"] or "Recapture" in checkout["description"]
        assert "signed-in user lands on `/shop/checkout`" not in checkout["description"]
        built = bp.page_ticket_draft(4, "/admin/")
        assert built["ready"] is True
        assert built["ready_reason"] == "stencil recorded"
        assert "- username" in built["description"]
        assert "password (password)" in built["description"]
        assert "Return to site → `/`" in built["description"]
        assert "a signed-in user lands on `/admin`" in built["description"]
        assert 'type=password' not in built["description"]
        assert "variant=" not in built["description"]
    finally:
        bp.plan_blueprint = original


def test_fastapi_inline_and_either_or_templates_are_pages_not_gaps(tmp_path: Path):
    from distr.core.planning import project_scan

    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "studio.html").write_text("<h1>Studio</h1>")
    (tmp_path / "templates" / "blueprint.html").write_text("<h1>Blueprint</h1>")
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI, Request\n"
        "from fastapi.responses import HTMLResponse\n"
        "from fastapi.templating import Jinja2Templates\n"
        "app = FastAPI()\n"
        "templates = Jinja2Templates(directory='templates')\n"
        "@app.get('/test', response_class=HTMLResponse)\n"
        "async def test_page():\n"
        "    return '<html><body>ok</body></html>'\n"
        "@app.get('/studio/', response_class=HTMLResponse)\n"
        "async def studio(request: Request):\n"
        "    template = 'blueprint.html' if request.query_params.get('mode') else 'studio.html'\n"
        "    return templates.TemplateResponse(request, template, {})\n"
    )
    model = project_scan.build_html_sitemap(tmp_path)
    pages = {node["route"]: node for node in model["nodes"] if node.get("kind") == "page"}
    assert pages["/test"]["status"] == "inferred"
    assert pages["/studio"]["status"] in {"verified", "inferred"}
    assert model["coverage"] in {"verified", "inferred"}
