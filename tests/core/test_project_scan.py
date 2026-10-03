"""Universal project → plan scan: same path for every board, not Merrypak-only."""

from __future__ import annotations

from pathlib import Path

from distr.core.planning.project_scan import scan_project


def _sport_fixture(root: Path) -> None:
    frontend = root / "frontend" / "src"
    frontend.mkdir(parents=True)
    (frontend / "routes.jsx").write_text(
        """
export const routes = [
  { url: "/", name: "home", element: <Home /> },
  { url: "/fixtures", name: "fixtures", element: <Fixtures /> },
  { url: "/match/:id", name: "match_detail", element: <Match /> },
  { url: "/teams", name: "teams", element: <Teams /> },
  { url: "/login", name: "login", element: <Login /> },
];
""",
        encoding="utf-8",
    )
    models = root / "backend" / "webapp" / "apps" / "sport" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text(
        """
from django.db import models

class Team(models.Model):
    name = models.CharField(max_length=120)

class Fixture(models.Model):
    home = models.ForeignKey("Team", on_delete=models.CASCADE, related_name="home_fixtures")
    away = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="away_fixtures")
""",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# PlayerOne Sport\nLive fixtures and scores.\n", encoding="utf-8")


def _auction_fixture(root: Path) -> None:
    (root / "apps" / "lots").mkdir(parents=True)
    (root / "apps" / "lots" / "urls.py").write_text(
        """
from django.urls import path
urlpatterns = [
    path("", views.home, name="home"),
    path("auctions/", views.auctions, name="auctions"),
    path("lots/<int:pk>/", views.lot_detail, name="lot_detail"),
    path("bids/", views.bids, name="bids"),
]
""",
        encoding="utf-8",
    )
    (root / "apps" / "lots" / "models.py").write_text(
        """
from django.db import models

class Auction(models.Model):
    title = models.CharField(max_length=200)

class Lot(models.Model):
    auction = models.ForeignKey(Auction, on_delete=models.CASCADE)
    reserve = models.DecimalField(max_digits=10, decimal_places=2)

class Bid(models.Model):
    lot = models.ForeignKey("Lot", on_delete=models.CASCADE)
""",
        encoding="utf-8",
    )


def _logistics_fixture(root: Path) -> None:
    js = root / "web" / "routes.js"
    js.parent.mkdir(parents=True)
    js.write_text(
        """
const routes = [
  { url: "/", name: "dashboard" },
  { url: "/shipments", name: "shipments" },
  { url: "/track/:code", name: "track_parcel" },
  { url: "/fleet", name: "fleet" },
];
""",
        encoding="utf-8",
    )
    models = root / "backend" / "tracking" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text(
        """
from django.db import models

class Shipment(models.Model):
    code = models.CharField(max_length=32)

class ParcelEvent(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE)
""",
        encoding="utf-8",
    )


def test_scan_sport_project_builds_routes_erd_and_requirements(tmp_path):
    root = tmp_path / "Player1Sport"
    root.mkdir()
    _sport_fixture(root)
    scanned = scan_project(root, project_name="Player1Sport")
    assert scanned["summary"]["route_count"] >= 4
    assert scanned["summary"]["model_count"] >= 2
    assert "Sport" in scanned["summary"]["route_groups"]
    artifacts = scanned["artifacts"]
    assert "/fixtures" in artifacts["prd"]
    assert "FR-001" in artifacts["frac"]
    assert "Fixture" in artifacts["architecture"]
    assert "Team" in artifacts["architecture"]
    assert "}o--||" in artifacts["architecture"] or "||--||" in artifacts["architecture"]
    assert 'screen "Overview · Player1Sport"' in artifacts["flows"]
    assert 'route="/fixtures"' in artifacts["flows"]
    assert "PlayerOne Sport" in artifacts["brief"]
    assert "/shop/basket" not in artifacts["flows"]
    assert scanned["summary"]["passes"] >= 1
    # Iterative deepen should attach real model fields on Auction/Sport-class apps.
    assert "string name" in artifacts["architecture"] or "name" in artifacts["architecture"]
    assert any(pass_row.get("gaps") for pass_row in scanned["passes"][:-1]) or scanned["adequate"]


def test_iterative_build_deepens_wireframes_from_page_cues(tmp_path):
    root = tmp_path / "AuctionLite"
    root.mkdir()
    frontend = root / "frontend" / "src"
    pages = frontend / "pages" / "auth"
    pages.mkdir(parents=True)
    (frontend / "routes.jsx").write_text(
        """
export const routes = [
  { url: "/", view: "home", name: "home" },
  { url: "/login", view: "auth/login", name: "login" },
  { url: "/auctions", view: "auction/list", name: "auctions" },
];
""",
        encoding="utf-8",
    )
    (pages / "login.tsx").write_text(
        """
export function Login() {
  const [formData, setFormData] = useState({ email: '', password: '' });
  return (
    <form>
      <TextField label="Email" />
      <TextField label="Password" />
      <button>Sign in</button>
    </form>
  );
}
""",
        encoding="utf-8",
    )
    (frontend / "pages" / "home.tsx").write_text("export function Home(){ return <button>Browse</button> }", encoding="utf-8")
    models = root / "backend" / "apps" / "accounts" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text(
        """
from django.db import models
class Profile(models.Model):
    email = models.EmailField()
    password_hint = models.CharField(max_length=40)
""",
        encoding="utf-8",
    )
    result = scan_project(root, project_name="AuctionLite")
    assert result["summary"]["cue_count"] >= 1
    assert "\n    form " in result["artifacts"]["flows"]
    assert "bind=Profile.email" in result["artifacts"]["flows"] or 'input "Email"' in result["artifacts"]["flows"]
    assert "without a server error" in result["artifacts"]["frac"]
    assert result["artifacts"]["frac"].count("AC-") > result["artifacts"]["frac"].count("without a server error")
    assert "## Domain constraints" in result["artifacts"]["prd"]
    assert result["adequate"] is True
    assert len(result["passes"]) >= 2


def test_scan_auction_project_uses_django_urls_when_no_frontend_routes(tmp_path):
    root = tmp_path / "AuctionNow"
    root.mkdir()
    _auction_fixture(root)
    scanned = scan_project(root, project_name="AuctionNow")
    assert scanned["summary"]["route_count"] >= 3
    assert "Auction" in scanned["summary"]["route_groups"]
    assert "Lot" in scanned["artifacts"]["architecture"]
    assert "Bid" in scanned["artifacts"]["architecture"]
    assert "/auctions" in scanned["artifacts"]["flows"] or "auctions" in scanned["artifacts"]["prd"]


def test_scan_logistics_project_groups_tracking_routes(tmp_path):
    root = tmp_path / "Spoortrack"
    root.mkdir()
    _logistics_fixture(root)
    scanned = scan_project(root, project_name="Spoortrack")
    assert scanned["summary"]["route_count"] >= 3
    assert "Logistics" in scanned["summary"]["route_groups"]
    assert "Shipment" in scanned["artifacts"]["architecture"]
    assert 'route="/shipments"' in scanned["artifacts"]["flows"]
    # Sibling nav is group-local, never Merrypak shop hardcodes.
    assert "/shop/checkout" not in scanned["artifacts"]["flows"]


def test_scan_empty_project_reports_zero_signal(tmp_path):
    root = tmp_path / "EmptyBoard"
    root.mkdir()
    scanned = scan_project(root, project_name="EmptyBoard")
    assert scanned["summary"]["route_count"] == 0
    assert scanned["summary"]["model_count"] == 0


def test_scan_flows_use_link_navigation_not_button_href(tmp_path):
    """Shallow journey maps must emit valid wire DSL (link route=), not button href=."""
    _sport_fixture(tmp_path)
    artifacts = scan_project(tmp_path, project_name="Player1Sport")["artifacts"]
    flows = artifacts["flows"]
    button_href = [line for line in flows.splitlines() if "button " in line and "href=" in line]
    assert button_href == [], button_href
    assert any(line.lstrip().startswith("link ") and "route=" in line for line in flows.splitlines())



def _screen_chunks(flows: str) -> dict[str, str]:
    chunks: dict[str, str] = {}
    for chunk in flows.strip().split("\n\n"):
        first = chunk.splitlines()[0]
        if first.startswith("screen "):
            chunks[first] = chunk
    return chunks


def test_route_screens_use_own_template_not_foreign_fields(tmp_path):
    """A route must not inherit another model's fields or sibling nav."""
    root = tmp_path / "Shop"
    pages = root / "frontend" / "src" / "pages"
    (pages / "core").mkdir(parents=True)
    (pages / "auth").mkdir()
    (root / "frontend" / "src" / "components" / "ui").mkdir(parents=True)
    (pages / "routes.jsx").write_text(
        """
export const routes = [
  { url: "/", view: "core_home", name: "home" },
  { url: "/login", view: "auth_login", name: "login" },
  { url: "/contact", view: "core_contact", name: "contact" },
  { url: "/about", view: "core_about", name: "about" },
];
""",
        encoding="utf-8",
    )
    (pages / "core" / "home.jsx").write_text(
        """
export function Home() {
  const [toast, setToast] = useState({ open: false, message: '', type: 'info', duration: 6000 });
  return (<div>
    <h3>Search by categories</h3>
    <button>Clear All</button>
    <Button>X</Button>
  </div>);
}
""",
        encoding="utf-8",
    )
    (pages / "auth" / "login.jsx").write_text(
        """
export function Login() {
  const [toast, setToast] = useState({ open: false, message: '', type: 'success' });
  return (<div>
    <header>Account</header>
    <div className="hero">Welcome back</div>
    <form>
      <Button>X</Button>
      <LabelText label="Email" type="email" name="email" placeholder="name@company.com" />
      <LabelText label="Password" type="password" name="password" placeholder="••••••••" />
      <LongButton>Login</LongButton>
      <DefaultLink to="/forgot-password">Forgot password?</DefaultLink>
    </form>
    <footer>Need help</footer>
  </div>);
}
""",
        encoding="utf-8",
    )
    (pages / "core" / "contact.jsx").write_text(
        """
import ContactBlock from "../../components/ui/ContactBlock";
export function Contact(){ return <section><ContactBlock /></section>; }
""",
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "components" / "ui" / "ContactBlock.jsx").write_text(
        """
export function ContactBlock(){
  return <form>
    <LabelText label="Your name" name="name" />
    <LabelText label="Message" name="message" />
    <LongButton>Send</LongButton>
  </form>;
}
""",
        encoding="utf-8",
    )
    (pages / "core" / "about.jsx").write_text(
        "export function About(){ return <section />; }\n",
        encoding="utf-8",
    )
    models = root / "backend" / "apps" / "shop" / "models.py"
    models.parent.mkdir(parents=True)
    models.write_text(
        """
from django.db import models
class ShopSettings(models.Model):
    openai_key = models.CharField(max_length=80)
class ContactMessage(models.Model):
    message = models.TextField()
class Profile(models.Model):
    email = models.EmailField()
    password_hint = models.CharField(max_length=40)
""",
        encoding="utf-8",
    )
    flows = scan_project(root, project_name="Shop")["artifacts"]["flows"]
    chunks = _screen_chunks(flows)
    home = next(chunk for title, chunk in chunks.items() if title.startswith('screen "Home"'))
    login = next(chunk for title, chunk in chunks.items() if title.startswith('screen "Login"'))
    contact = next(chunk for title, chunk in chunks.items() if title.startswith('screen "Contact"'))
    about = next(chunk for title, chunk in chunks.items() if title.startswith('screen "About"'))
    overview = next(chunk for title, chunk in chunks.items() if "Overview" in title)
    assert "openai_key" not in home
    assert "ShopSettings" not in home
    assert "ContactMessage" not in home
    assert 'section "Search by categories"' in home
    assert 'group Core' in home
    assert '\n    form ' not in home
    assert 'button "X"' not in login
    assert 'input "Email" type=email' in login
    assert 'input "Password" type=password' in login
    assert 'button "Login"' in login
    assert "password_hint" not in login
    assert 'header "Account"' in login
    assert 'hero "Welcome back"' in login
    assert 'footer "Need help"' in login
    assert 'link "Forgot password?" route="/forgot-password"' in login
    assert 'input "Your name"' in contact
    assert 'input "Message"' in contact
    assert 'button "Send"' in contact
    assert 'link "' not in contact
    assert "No fields or sections were found in this route's own template." in about
    assert 'link "' not in about
    assert "openai_key" not in about
    assert 'heading "Account"' in overview or 'heading "Core"' in overview
    assert 'link "Home"' in overview



def _html_shop(root: Path) -> None:
    """Django templates, views, and urls. A React route table is present and must be ignored."""
    templates = root / "shop" / "templates"
    templates.mkdir(parents=True)
    (templates / "base.html").write_text(
        "<!doctype html>{% include 'header.html' %}<main>{% block content %}{% endblock %}</main>\n",
        encoding="utf-8",
    )
    (templates / "header.html").write_text("<header>Shop</header>\n", encoding="utf-8")
    (templates / "home.html").write_text(
        "{% extends 'base.html' %}\n"
        "<a href=\"{% url 'auctions' %}\">Auctions</a>\n"
        "<a href=\"{% url 'auctions' %}\">Continue</a>\n",
        encoding="utf-8",
    )
    (templates / "auctions.html").write_text(
        "{% extends 'base.html' %}<a href=\"/lots/1/\">Open lot</a>\n",
        encoding="utf-8",
    )
    (templates / "lot.html").write_text("{% extends 'base.html' %}<p>Lot</p>\n", encoding="utf-8")
    (templates / "bid.html").write_text("{% extends 'base.html' %}<form></form>\n", encoding="utf-8")
    (templates / "login.html").write_text("{% extends 'base.html' %}<form></form>\n", encoding="utf-8")
    (templates / "catalog.html").write_text("{% extends 'base.html' %}<section>Catalog</section>\n", encoding="utf-8")
    (templates / "guessed.html").write_text("{% extends 'base.html' %}<p>Guessed</p>\n", encoding="utf-8")
    (templates / "about.html").write_text("{% extends 'base.html' %}<p>About</p>\n", encoding="utf-8")
    (root / "shop" / "views.py").write_text(
        """
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.generic import TemplateView

def home(request):
    return render(request, "home.html")

def auctions(request):
    return render(request, "auctions.html")

class LotDetail(TemplateView):
    template_name = "lot.html"

def login(request):
    return render(request, "login.html")

@login_required
def bid(request):
    if form.is_valid():
        return redirect("lot_detail")
    else:
        return redirect("auctions")
    return render(request, "bid.html")

def legacy(request):
    return redirect("/auctions/")

def missing_page(request):
    return render(request, "gone.html")

def product_list(request):
    return render(request, "catalog.html")

def product_search(request):
    return render(request, "catalog.html")

def guessed(request):
    template = "guessed.html"
    return render(request, template)
""",
        encoding="utf-8",
    )
    (root / "shop" / "urls.py").write_text(
        """
from django.urls import path
from . import views
urlpatterns = [
    path("", views.home, name="home"),
    path("auctions/", views.auctions, name="auctions"),
    path("lots/<int:pk>/", views.LotDetail.as_view(), name="lot_detail"),
    path("login/", views.login, name="login"),
    path("bids/", views.bid, name="bid"),
    path("old/", views.legacy, name="legacy"),
    path("ghost/", views.missing_page, name="ghost"),
    path("products/", views.product_list, name="product_list"),
    path("products/search/", views.product_search, name="product_search"),
    path("guessed/", views.guessed, name="guessed"),
]
""",
        encoding="utf-8",
    )
    frontend = root / "frontend" / "src"
    frontend.mkdir(parents=True)
    (frontend / "routes.jsx").write_text(
        'export const routes = [{ url: "/from-react", name: "from_react" }];\n',
        encoding="utf-8",
    )


def _nodes_by_route(model):
    return {node["route"]: node for node in model["nodes"] if node.get("route")}


def _edge_kinds(model, source_route, target_route):
    ids = {node["id"]: node for node in model["nodes"]}
    source = next(node["id"] for node in model["nodes"] if node.get("route") == source_route)
    target = next(node["id"] for node in model["nodes"] if node.get("route") == target_route)
    return sorted(edge["kind"] for edge in model["edges"] if edge["from"] == source and edge["to"] == target and ids[edge["to"]]["kind"] == "page")


def test_html_sitemap_resolves_template_view_and_url(tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    _html_shop(root)
    from distr.core.planning.project_scan import build_html_sitemap

    model = build_html_sitemap(root)
    pages = _nodes_by_route(model)
    assert pages["/"]["status"] == "verified"
    assert pages["/"]["template"] == "home.html"
    assert pages["/"]["view"] == "home"
    assert pages["/auctions"]["status"] == "verified"
    assert pages["/lots/:pk"]["status"] == "verified"
    assert pages["/lots/:pk"]["view"] == "LotDetail"
    assert pages["/lots/:pk"]["template"] == "lot.html"
    assert pages["/guessed"]["status"] == "inferred"
    assert pages["/ghost"]["status"] == "missing"
    assert pages["/old"]["status"] == "incomplete"
    assert "navigation" in _edge_kinds(model, "/", "/auctions")
    assert "journey" in _edge_kinds(model, "/", "/auctions")
    assert "navigation" in _edge_kinds(model, "/auctions", "/lots/:pk")
    assert "success" in _edge_kinds(model, "/bids", "/lots/:pk")
    assert "failure" in _edge_kinds(model, "/bids", "/auctions")
    assert "redirect" in _edge_kinds(model, "/old", "/auctions")
    assert "auth" in _edge_kinds(model, "/login", "/bids")
    assert "parent" in _edge_kinds(model, "/products", "/products/search")
    catalog = next(node for node in model["nodes"] if node["kind"] == "page_template" and node["template"] == "catalog.html")
    child = pages["/products"]["id"]
    assert any(edge["kind"] == "parent" and edge["from"] == catalog["id"] and edge["to"] == child for edge in model["edges"])
    about = next(node for node in model["nodes"] if node["kind"] == "page_template" and node["template"] == "about.html")
    assert about["status"] == "incomplete"
    assert "/from-react" not in pages
    assert all(node["status"] in {"verified", "inferred", "incomplete", "missing"} for node in model["nodes"])


def test_html_sitemap_excludes_base_templates_from_nodes(tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    _html_shop(root)
    from distr.core.planning.project_scan import build_html_sitemap

    model = build_html_sitemap(root)
    templates = {node.get("template") for node in model["nodes"]}
    assert "base.html" not in templates
    assert "header.html" not in templates
    scaffolding = {item["template"] for item in model["scaffolding"]}
    assert "base.html" in scaffolding
    assert "header.html" in scaffolding
    assert all(item["kind"] == "base_template" for item in model["scaffolding"])
    assert all(node["kind"] in {"page", "page_template"} for node in model["nodes"])


def test_html_sitemap_cache_written_on_first_scan(tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    _html_shop(root)
    cache = tmp_path / "cache" / "sitemap.json"
    from distr.core.planning.project_scan import ensure_sitemap_cache

    assert not cache.exists()
    model = ensure_sitemap_cache(root, cache_path=cache)
    assert cache.is_file()
    assert model["source"] == "html-views-urls"
    assert any(node["route"] == "/auctions" and node["status"] == "verified" for node in model["nodes"])
    again = ensure_sitemap_cache(root, cache_path=cache)
    assert again["nodes"] == model["nodes"]


def test_html_sitemap_drift_schedules_rebuild_without_scan_button(tmp_path, monkeypatch):
    root = tmp_path / "shop"
    root.mkdir()
    _html_shop(root)
    cache = tmp_path / "cache" / "sitemap.json"
    import distr.core.planning.project_scan as project_scan

    first = project_scan.ensure_sitemap_cache(root, cache_path=cache)
    assert "/reports" not in {node.get("route") for node in first["nodes"]}
    urls = root / "shop" / "urls.py"
    urls.write_text(
        urls.read_text(encoding="utf-8").replace(
            'path("guessed/", views.guessed, name="guessed"),',
            'path("guessed/", views.guessed, name="guessed"),\n    path("reports/", views.home, name="reports"),',
        ),
        encoding="utf-8",
    )

    class ImmediateThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            assert self.daemon is True
            assert self.name == "sitemap-rebuild"
            self.target()

    monkeypatch.setattr(project_scan.threading, "Thread", ImmediateThread)
    stale = project_scan.ensure_sitemap_cache(root, cache_path=cache)
    assert "/reports" not in {node.get("route") for node in stale["nodes"]}
    rebuilt = project_scan.ensure_sitemap_cache(root, cache_path=cache)
    reports = next(node for node in rebuilt["nodes"] if node.get("route") == "/reports")
    assert reports["view"] == "home"
    assert reports["status"] == "verified"


def test_react_only_project_sitemap_is_missing_not_invented(tmp_path):
    root = tmp_path / "ReactOnly"
    frontend = root / "frontend" / "src"
    frontend.mkdir(parents=True)
    (frontend / "routes.jsx").write_text(
        'export const routes = [{ url: "/only-react", name: "only_react" }];\n',
        encoding="utf-8",
    )
    (frontend / "pages").mkdir()
    (frontend / "pages" / "Only.jsx").write_text("export function Only(){ return <main/> }\n", encoding="utf-8")
    from distr.core.planning.project_scan import build_html_sitemap

    model = build_html_sitemap(root)
    assert model["nodes"] == []
    assert model["edges"] == []
    assert model["coverage"] == "missing"
    assert "React" in model["note"]
    assert all("only-react" not in str(node) for node in model["nodes"])
