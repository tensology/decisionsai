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
