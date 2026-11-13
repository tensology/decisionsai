"""Template-expression hrefs (e.g. Django {{ ... }}) must not fail the whole project scan."""
from distr.core.planning.project_scan import _screen_lines, _wire_route
from distr.core.planning.validation import validate_wireframe


def test_wire_route_turns_template_expressions_into_placeholders():
    assert _wire_route("/admin/translations/files/project/{{ language.iso_code }}/0") == "/admin/translations/files/project/{language.iso_code}/0"
    assert _wire_route("/plain/route") == "/plain/route"
    assert _wire_route("#anchor") == "#anchor"
    assert _wire_route("{url}?{qs}") is None
    assert _wire_route("https://example.com/x") is None
    assert _wire_route("//evil.example/x") is None
    assert _wire_route("/a b") is None


def test_screen_with_template_route_validates():
    row = {"url": "/files/{{ language.iso_code }}/0", "title": "Files", "group": "Admin", "name": "files"}
    source = "\n".join(_screen_lines(row, models=[], cues={}, fr_id=None))
    validate_wireframe(source)   # raises PlanValidationError on an invalid route
    assert 'route="/files/{language.iso_code}/0"' in source


def test_unroutable_screen_keeps_the_page_without_a_route():
    row = {"url": "{url}?{qs}", "title": "Query page", "group": "Core", "name": "q"}
    source = "\n".join(_screen_lines(row, models=[], cues={}, fr_id=None))
    validate_wireframe(source)
    assert source.startswith('screen "Query page" device=desktop\n')
