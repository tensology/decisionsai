"""Controls, flow map, source mapping, base style, dry-run page render and its scoring."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from distr.core.planning import base_style, control_map, controls, flow_map, page_render, render_score


# ---------------------------------------------------------------- controls

def test_route_of_classifies_every_link_kind():
    origin = "https://shop.test"
    assert controls._route_of("", origin, "") == ("empty", "")
    assert controls._route_of("#", origin, "") == ("empty", "")
    assert controls._route_of("javascript:void(0)", origin, "") == ("script", "")
    assert controls._route_of("mailto:a@b.c", origin, "")[0] == "mailto"
    assert controls._route_of("tel:+27", origin, "")[0] == "tel"
    assert controls._route_of("#top", origin, "") == ("anchor", "#top")
    assert controls._route_of("/cart?x=1", origin, origin + "/") == ("route", "/cart?…")
    assert controls._route_of("https://elsewhere.test/x", origin, origin + "/") == ("external", "elsewhere.test")
    assert controls._route_of("../basket", origin, origin + "/shop/item")[1] == "/basket"


class _Req:
    def __init__(self, url, status, previous=None):
        self.url, self._status, self.redirected_from = url, status, previous

    def response(self):
        return SimpleNamespace(status=self._status)


def test_nav_info_detects_a_login_gate_and_a_denied_page():
    first = _Req("https://s.test/admin/", 302)
    last = _Req("https://s.test/admin/login/?next=/admin/", 200, first)
    response = SimpleNamespace(request=last, status=200, headers={"content-type": "text/html; charset=utf-8"})
    page = SimpleNamespace(url="https://s.test/admin/login/?next=/admin/")
    info = controls.nav_info("https://s.test/admin/", page, response)
    assert info["gate"] == "login" and info["redirected"] and info["redirects"][0]["status"] == 302
    assert info["requested"] == "/admin/" and info["final"] == "/admin/login/"

    denied = controls.nav_info("https://s.test/api", SimpleNamespace(url="https://s.test/api"),
                               SimpleNamespace(request=_Req("https://s.test/api", 403), status=403, headers={}))
    assert denied["gate"] == "denied" and not denied["redirected"]

    plain = controls.nav_info("https://s.test/a", SimpleNamespace(url="https://s.test/a"),
                              SimpleNamespace(request=_Req("https://s.test/a", 200), status=200, headers={}))
    assert plain["gate"] == "" and not plain["redirected"]


def test_sanitize_keeps_only_the_known_shape_and_bounds_strings():
    dirty = {"title": "T\x00itle", "links": [{"text": "x" * 900, "kind": "route", "to": "/a", "evil": "<script>"}, "junk"],
             "forms": [{"method": "POST", "action": "/go", "fields": [{"name": "a"}], "extra": 1}], "unknown": {"a": 1}}
    clean = controls.sanitize(dirty)
    assert "\x00" not in clean["title"]
    assert len(clean["links"]) == 1 and len(clean["links"][0]["text"]) <= 200 and "evil" not in clean["links"][0]
    assert "extra" not in clean["forms"][0] and "unknown" not in clean
    assert controls.sanitize("nope") == {} and controls.sanitize(None) == {}


# ---------------------------------------------------------------- flow map

def _pages():
    chrome = [{"text": "Home", "kind": "route", "to": "/", "in": "header"}, {"text": "Cart", "kind": "route", "to": "/cart", "in": "header"}]
    def page(route, extra_links=(), forms=(), nav=None):
        return {"controls": {"links": [*chrome, *extra_links], "forms": list(forms), "nav": nav or {"requested": route, "final": route, "redirected": False}}, "style": {}}
    return {
        "/": page("/", [{"text": "Phone", "kind": "route", "to": "/p/phone", "in": "body"}]),
        "/p/phone": page("/p/phone", forms=[{"method": "POST", "action": "/cart/add", "kind": "route", "submit": ["Add to cart"]}]),
        "/cart": page("/cart"),
        "/account": page("/account", nav={"requested": "/account", "final": "/login", "redirected": True, "gate": "login", "redirects": [{"url": "/account", "status": 302}]}),
    }


def test_edges_collapse_site_chrome_and_classify_kinds():
    es, site_nav = flow_map.edges(_pages())
    kinds = {(e["from"], e["to"], e["kind"]) for e in es}
    assert ("/", "/p/phone", "link") in kinds
    assert ("/p/phone", "/cart/add", "form") in kinds
    assert ("/account", "/login", "gate") in kinds
    assert not any(e["kind"] == "nav" and e["to"] in ("/", "/cart") for e in es), "chrome links are drawn once, not per page"
    assert {n["to"] for n in site_nav} >= {"/", "/cart"}


def test_flow_wire_is_valid_and_mermaid_names_every_page():
    lines = flow_map.wire_lines(_pages(), "Shop")
    assert lines[0].startswith('screen "Flow map · Shop"') and all('"' in l or l.startswith("  ") for l in lines)
    assert any("sign-in gate" in l for l in lines)
    text = flow_map.mermaid(_pages(), "Shop")
    assert text.startswith("flowchart") and "/p/phone" in text and "/account" in text


def test_controls_markdown_lists_behaviour_and_mapping():
    pages = _pages()
    md = flow_map.controls_markdown("/p/phone", pages["/p/phone"]["controls"], {"page": {"view": "shop/views.py:12"}, "controls": {}})
    assert "## Controls" in md and "Add to cart" in md and "shop/views.py:12" in md
    gated = flow_map.controls_markdown("/account", pages["/account"]["controls"])
    assert "sign-in" in gated and "/login" in gated


def test_captured_pages_reads_scratch_metas_only(tmp_path):
    folder = tmp_path / "planning"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps({"url": "https://s.test/shop/", "controls": {"links": [{"text": "x", "kind": "route", "to": "/y"}]}}))
    (folder / "bad.json").write_text("{not json")
    (folder / "nocontrols.json").write_text(json.dumps({"url": "https://s.test/z"}))
    assert set(flow_map.captured_pages(tmp_path)) == {"/shop"}


# ---------------------------------------------------------------- source mapping

def test_django_mapping_finds_view_and_form(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "urls.py").write_text("from django.urls import path\nfrom . import views\nurlpatterns = [path('login/', views.login_view, name='login')]\n")
    (tmp_path / "app" / "views.py").write_text("def login_view(request):\n    return render(request, 'login.html')\n")
    (tmp_path / "app" / "forms.py").write_text("from django import forms\nclass LoginForm(forms.Form):\n    email = forms.EmailField()\n    password = forms.CharField()\n")
    (tmp_path / "app" / "templates").mkdir()
    (tmp_path / "app" / "templates" / "login.html").write_text('<form method="post"><a href="/register/">Register</a>{{ form.email }}</form>')
    pages = {"/login": {"controls": {"framework": "django", "nav": {"requested": "/login", "final": "/login/"},
                                     "links": [{"text": "Register", "kind": "route", "to": "/register/", "in": "body"}],
                                     "forms": [{"method": "POST", "action": "/login/", "kind": "route",
                                                "fields": [{"name": "email"}, {"name": "password"}], "submit": ["Log in"]}]}, "style": {}}}
    mapped = control_map.map_project(tmp_path, pages)
    page = mapped.get("/login", {}).get("page", {})
    assert "login_view" in json.dumps(page) and "LoginForm" in json.dumps(mapped["/login"])


def test_mapping_is_silent_when_the_source_has_nothing(tmp_path):
    pages = {"/x": {"controls": {"framework": "django", "links": [{"text": "A", "kind": "route", "to": "/a/"}], "forms": [], "nav": {}}, "style": {}}}
    assert not control_map.map_project(tmp_path, pages).get("/x", {}).get("page")


# ---------------------------------------------------------------- base style

def _raw(button_bg=(213, 0, 50)):
    rec = lambda k, t, box, **x: {"k": k, "t": t, "r": box, "d": "block", **x}
    white, ink = [255, 255, 255, 1], [17, 17, 17, 1]
    return {"desktop": {"records": [
        rec(1, "body", [0, 0, 1440, 900], bg=white, ff="Inter"),
        rec(2, "p", [0, 0, 600, 24], tb=True, fz=16, fg2=ink, ff="Inter"),
        rec(3, "button", [0, 100, 120, 40], bg=[*button_bg, 1], fg2=white, fz=14, rad=5, fw=600, ff="Inter", tb=True),
        rec(4, "input", [0, 200, 300, 38], bg=white, fz=16, rad=6, bdw=[1, 1, 1, 1], ff="Inter"),
    ]}}


def test_style_summary_reads_button_and_font():
    s = base_style.summarise(_raw())
    assert s["font"] == "Inter" and s["size"] == 16
    assert s["button"]["bg"].lower() == "#d50032" and s["button"]["radius"] == 5
    assert base_style.summarise({}) == {}


def test_style_aggregate_takes_the_modal_value_and_css_uses_it():
    agg = base_style.aggregate([base_style.summarise(_raw()), base_style.summarise(_raw()), base_style.summarise(_raw((0, 0, 0)))])
    assert agg["button"]["bg"].lower() == "#d50032"
    css = base_style.to_css(agg)
    assert "#d50032" in css and "Inter" in css
    assert base_style.stencil_lines(agg, "Shop")[0].startswith('screen "Base template · Shop"')


# ---------------------------------------------------------------- dry-run page render

def test_sanitise_html_removes_scripts_handlers_and_remote_resources():
    dirty = ('<!doctype html><html><head><script>alert(1)</script><link rel="stylesheet" href="https://x.test/a.css">'
             '<style>@import "https://x.test/b.css";.a{background:url(https://x.test/i.png)}</style></head>'
             '<body onload="x()"><img src="https://x.test/p.png"><a href="javascript:go()" onclick="y()">hi</a></body></html>')
    clean = page_render.sanitise_html(dirty)
    for bad in ("<script", "onload", "onclick", "x.test", "javascript:", "<link", "@import"):
        assert bad not in clean
    assert "<a " in clean and "hi" in clean


def test_parse_reply_takes_json_or_a_bare_document_and_refuses_prose():
    doc = "<!doctype html><html><body><p>x</p></body></html>"
    assert "<p>x</p>" in page_render.parse_reply(json.dumps({"html": doc}))
    assert "<p>x</p>" in page_render.parse_reply("```json\n" + json.dumps({"html": doc}) + "\n```")
    assert "<p>x</p>" in page_render.parse_reply("here you go\n" + doc)
    with pytest.raises(ValueError):
        page_render.parse_reply("I cannot do that.")


def test_dry_run_refuses_to_write_inside_a_project_folder(tmp_path):
    project = tmp_path / "client"
    project.mkdir()
    with pytest.raises(ValueError):
        page_render.check_output_dir(project / "out", extra_forbidden=[project.resolve()])
    scratch = tmp_path / "scratch"
    assert page_render.check_output_dir(scratch, extra_forbidden=[project.resolve()]) == scratch.resolve()


def test_prompt_carries_stencil_style_and_controls_but_no_secrets():
    msgs = page_render.build_messages(route="/login", stencil=['screen "x" device=desktop layout=v2', "  stack"],
                                      style={"font": "Inter", "size": 16, "button": {"bg": "#d50032"}}, controls={"links": [], "forms": []})
    text = json.dumps(msgs)
    assert "layout=v2" in text and "#d50032" in text and msgs[0]["role"] == "system"


# ---------------------------------------------------------------- scoring

def _png(path, bars, size=(400, 300), bg=255, ink=0):
    arr = np.full((size[1], size[0], 3), bg, dtype=np.uint8)
    for x, y, w, h in bars:
        arr[y:y + h, x:x + w] = ink
    Image.fromarray(arr).save(path)
    return str(path)


def test_scores_reward_matching_layout_and_ignore_polarity(tmp_path):
    bars = [(20, 20, 200, 20), (20, 80, 300, 40), (20, 160, 100, 20)]
    a = _png(tmp_path / "a.png", bars)
    same = _png(tmp_path / "same.png", bars)
    moved = _png(tmp_path / "moved.png", [(200, 220, 150, 40)])
    dark = _png(tmp_path / "dark.png", bars, bg=20, ink=235)
    read = {"ph": 300, "pw": 400, "records": []}
    assert render_score.score(read, read, a, same, stencil=True)["layout"] > 0.95
    assert render_score.score(read, read, a, moved, stencil=True)["layout"] < 0.6
    assert render_score.structure_score(a, dark) > 0.9
    assert render_score.ink_score(a, a) == (1.0, 1.0)
    short = {"ph": 150, "pw": 400, "records": []}
    assert render_score.score(read, short, a, same, stencil=True)["height"] == 0.5
