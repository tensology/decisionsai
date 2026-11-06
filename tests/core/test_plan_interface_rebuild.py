"""N4 overlay rebuild of Interfaces (pure functions; no DB)."""
from distr.core.planning import interface_rebuild as ir

GEN1 = '''screen "Overview · Shop" device=desktop route="/"
  stack
    heading "Shop journey map"

screen "Customers" device=desktop route="/customers"
  stack
    heading "Customers"

screen "Orders" device=desktop route="/orders"
  stack
    heading "Orders"
'''
GEN2 = GEN1.replace('heading "Customers"', 'heading "Customers v2"').replace('''
screen "Orders" device=desktop route="/orders"
  stack
    heading "Orders"
''', '''
screen "Invoices" device=desktop route="/invoices"
  stack
    heading "Invoices"
''')
SIDE = '# decisions-flow: {"nodes":{"customers":{"shape":"circle"}},"v":1}'


def test_first_rebuild_of_untouched_scan_output_marks_everything():
    content, s = ir.overlay(GEN1, GEN1, revision=1)
    assert s["first_rebuild"] and content.count("# @generated scan:") == 3
    assert "# @generated scan:overview " in content and "# @generated scan:/customers " in content


def test_unmarked_edited_items_are_hand_made():
    content, s = ir.overlay(GEN1, GEN2, revision=3)
    assert s == {**s, "regenerated": 0, "kept": 3, "stale": 0, "added": 1}
    assert 'heading "Customers"' in content and 'route="/orders"' in content and 'route="/invoices"' in content


def test_overlay_regenerates_untouched_keeps_edited_flags_stale_and_preserves_annotations():
    marked = ir.mark_flows(GEN1)
    lines = marked.split("\n")
    i = next(n for n, l in enumerate(lines) if 'route="/customers"' in l)
    lines[i] = lines[i] + " id=customers"
    lines.insert(i + 1, "#| The customer list.")
    j = next(n for n, l in enumerate(lines) if 'heading "Shop journey map"' in l)
    lines[j] = '    heading "My own map"'                                   # hand edit of the overview
    previous = "\n".join(lines).rstrip("\n") + "\n" + SIDE + "\n"
    content, s = ir.overlay(previous, GEN2, revision=4)
    assert (s["regenerated"], s["kept"], s["stale"], s["added"]) == (1, 1, 1, 1)
    assert 'heading "Customers v2"' in content and "id=customers" in content and "#| The customer list." in content
    assert content.index("#| The customer list.") == content.index("id=customers") + len("id=customers") + 1   # directly under the screen
    assert 'heading "My own map"' in content                                   # hand-made kept
    assert 'screen "Orders (stale)"' in content                                # vanished route flagged, not deleted
    assert 'route="/invoices"' in content
    assert content.rstrip("\n").split("\n")[-1] == SIDE                        # sidecar stays last
    again, s2 = ir.overlay(content, GEN2, revision=5)                          # idempotent
    assert again == content and s2["stale"] == 0 and s2["added"] == 0
    assert "Interfaces: 1 regenerated, 1 added, 1 kept (hand-made), 1 stale." == ir.summary_text(s)


# ---- N4 widen: sitemap + routes union, template form fields (temp project; read-only scan) ----------------
from distr.core.planning import project_scan as ps  # noqa: E402

FAKE = "FAKE-not-a-real-token-123"


def _project(root):
    front = root / "frontend" / "src"
    front.mkdir(parents=True)
    (front / "routes.jsx").write_text('export const routes = [\n  { url: "/customers", name: "customers" },\n];\n', encoding="utf-8")
    app = root / "shop"
    (app / "templates" / "shop").mkdir(parents=True)
    (app / "urls.py").write_text(
        "from django.urls import path\nfrom . import views\nurlpatterns = [\n"
        "    path('customers/', views.customers, name='customers'),\n"
        "    path('contact/', views.contact, name='contact'),\n]\n", encoding="utf-8")
    (app / "views.py").write_text(
        "from django.shortcuts import render\n\ndef customers(request):\n    return render(request, 'shop/customers.html')\n\n"
        "def contact(request):\n    return render(request, 'shop/contact.html')\n", encoding="utf-8")
    (app / "templates" / "shop" / "customers.html").write_text("<h1>Customers</h1>\n", encoding="utf-8")
    (app / "templates" / "shop" / "contact.html").write_text(
        '<h1>Contact us</h1>\n<form method="post">\n'
        '  <input type="email" name="email" value="someone@example.com">\n'
        '  <textarea name="message"></textarea>\n'
        f'  <input type="hidden" name="api_token" value="{FAKE}">\n'
        '  <button type="submit">Send message</button>\n</form>\n', encoding="utf-8")
    (root / ".env").write_text(f"SECRET_KEY={FAKE}\n", encoding="utf-8")


def test_widen_unions_sitemap_routes_and_reads_template_fields(tmp_path):
    root = tmp_path / "proj"
    _project(root)
    scanned = ps.scan_project(root, project_name="Shop", max_passes=1)
    flows = scanned["artifacts"]["flows"]
    assert scanned["summary"]["widen"]["routes_added"] == 1 and scanned["summary"]["widen"]["template_cues"] >= 1
    assert flows.count('route="/customers"') == 2            # overview link + its own screen, not duplicated by the sitemap
    contact = flows[flows.index('screen "Contact"'):]
    assert 'input "Email" type=email' in contact and 'input "Message"' in contact and "Send message" in contact
    assert FAKE not in flows and "someone@example.com" not in flows      # never values, never .env
    assert FAKE not in str(scanned)


def test_widen_rebuild_is_idempotent_and_keeps_hand_edits(tmp_path):
    root = tmp_path / "proj"
    _project(root)
    first = ps.scan_project(root, project_name="Shop", max_passes=1)["artifacts"]["flows"]
    content, _ = ir.overlay(first, first, revision=1)
    again, s = ir.overlay(content, ps.scan_project(root, project_name="Shop", max_passes=1)["artifacts"]["flows"], revision=2)
    assert again == content and s["regenerated"] >= 3 and s["added"] == 0
    edited = again.replace('heading "Contact"', 'heading "Talk to us"', 1)
    assert edited != again
    kept, s2 = ir.overlay(edited, first, revision=3)
    assert 'heading "Talk to us"' in kept and s2["kept"] == 1


def test_template_cue_bounds():
    assert ps._template_cue("<input name='a'>" + "x" * 300_000) is None
    assert ps._template_cue("<input name='email'>\x00") is None
    cue = ps._template_cue("".join(f"<input name='f{i}x' type='text'>" for i in range(20)))
    assert len(cue["fields"]) == 6
