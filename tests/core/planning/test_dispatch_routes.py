"""A hand-rolled React router (``if (page === "/x") return <Page/>``) is the sitemap."""
from distr.core.planning import project_scan as ps

MAIN = '''
import ContactPage from "./ContactPage.jsx";
import { LoginPage, RegisterPage } from "./AuthPages.jsx";
import Home from "./Home.jsx";
function App() {
  const editMatch = page.match(/^\\/(yachts|charters)\\/(\\d+)\\/edit$/);
  const shareMatch = page.match(/^\\/(?:charters\\/share|trip-preview)\\/([^/]+)\\/?$/);
  if (page === "/" && (!authChecked || user)) return <div className="loading">Loading</div>;
  if (page === "/contact") return <ContactPage go={go} />;
  if (page === "/login") return <LoginPage go={go} />;
  if (page === "/onboarding" && !user) return <LoginPage go={go} />;
  if (page === "/onboarding" && user.limited) return <RegisterPage go={go} />;
  if (page === "/onboarding") return <Shell user={user}><Home go={go} /></Shell>;
  if (shareMatch) return <Suspense fallback={<div />}><Home token={shareMatch[1]} /></Suspense>;
  if (editMatch && editMatch[1] === "charters") return <Shell><ContactPage id={editMatch[2]} notFound={<Missing />} /></Shell>;
  if (editMatch) return <Shell><Home id={editMatch[2]} /></Shell>;
  return <Home />;
}
'''


def _app(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.jsx").write_text(MAIN)
    (src / "ContactPage.jsx").write_text("export default function ContactPage(){return <form><label>Email</label><input type='email' name='email'/><button>Send</button></form>}")
    (src / "AuthPages.jsx").write_text(
        "export function LoginPage(){return <form><h1>Sign in</h1><label>Password</label><input type='password' name='password'/><button>Sign in</button></form>}\n"
        "export function RegisterPage(){return <form><h1>Create account</h1><button>Register</button></form>}\n")
    (src / "Home.jsx").write_text("export default function Home(){return <h1>Home</h1>}")
    (src / "Unrouted.jsx").write_text("export default function Unrouted(){return <h1>Not a page</h1>}")
    return tmp_path


def test_dispatch_routes_are_the_pages(tmp_path):
    rows = {row["url"]: row for row in ps.scan_react_pages(_app(tmp_path))}
    assert set(rows) == {"/contact", "/login", "/onboarding", "/charters/share/:token", "/trip-preview/:token",
                         "/charters/:id/edit", "/yachts/:id/edit"}
    assert rows["/onboarding"]["view"] == "Home"           # guard and user-flag variants do not win
    assert rows["/charters/:id/edit"]["view"] == "ContactPage"
    assert rows["/yachts/:id/edit"]["view"] == "Home"
    assert rows["/trip-preview/:token"]["view"] == "Home"  # wrappers (Suspense, Shell) are skipped


def test_dispatch_cues_come_from_the_named_component_only(tmp_path):
    rows = {row["url"]: row for row in ps.scan_react_pages(_app(tmp_path))}
    login = rows["/login"]
    assert login["source"] == "src/AuthPages.jsx"
    assert "Sign in" in login["cue"]["buttons"] and "Register" not in login["cue"]["buttons"]


def test_regex_routes_expand_alternatives():
    assert ps._regex_routes(r"^\/(yachts|charters)\/new$") == [("/yachts/new", ["yachts"]), ("/charters/new", ["charters"])]
    assert ps._regex_routes(r"^\/onboarding\/approve\/([0-9a-f-]{36})$")[0][0] == "/onboarding/approve/:uuid"


def test_aside_region_is_drawn_as_a_sidebar():
    lines = ps._screen_lines({"title": "Docs", "url": "/docs", "name": "docs"}, models=[],
                             cues={"/docs": {"regions": {"aside": "Contents"}}}, fr_id=None)
    assert any(line.strip().startswith('sidebar "Contents"') for line in lines)
    assert not any(line.strip().startswith("aside") for line in lines)
