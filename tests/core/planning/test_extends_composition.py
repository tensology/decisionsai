"""A page is base + page: the shell and the body both belong in the stencil."""

from distr.core.planning import project_scan as ps


def _templates():
    base = (
        "<html><body>\n"
        "<header><nav><a href='/'>Home</a><a href='/about'>About</a></nav></header>\n"
        "{% block content %}{% endblock %}\n"
        "<footer><a href='/privacy'>Privacy</a></footer>\n"
        "</body></html>\n"
    )
    page = (
        "{% extends 'base.html' %}\n"
        "{% block content %}\n"
        "<h1>Account settings</h1>\n"
        "<form><input name='email' label='Email' type='email'><button>Save</button></form>\n"
        "{% endblock %}\n"
    )
    return {
        "base.html": {"key": "base.html", "path": "templates/base.html", "text": base, "extends": "", "includes": []},
        "account.html": {"key": "account.html", "path": "templates/account.html", "text": page, "extends": "base.html", "includes": []},
    }


def test_a_page_cue_includes_its_base_shell():
    templates = _templates()
    cue = ps._compose_template_cue("account.html", templates)
    assert cue is not None
    # the page's own content
    assert "Email" in cue["fields"]
    assert "Save" in cue["buttons"]
    # and the shell it inherits
    links = {link["label"] for link in cue["links"]}
    assert {"Home", "About", "Privacy"} <= links


def test_a_page_with_no_extends_is_just_itself():
    templates = _templates()
    cue = ps._compose_template_cue("base.html", templates)
    assert cue is not None
    assert "Email" not in cue["fields"]


def test_the_page_wins_over_the_base_on_a_shared_key():
    templates = _templates()
    templates["account.html"]["text"] = (
        "{% extends 'base.html' %}{% block content %}"
        "<input name='page_field' label='Page field'>{% endblock %}"
    )
    cue = ps._compose_template_cue("account.html", templates)
    assert "Page field" in cue["fields"]
    # and the base's own field is still there, merged
    assert "Email" not in cue["fields"]


def test_a_cycle_does_not_hang():
    templates = {
        "a.html": {"key": "a.html", "path": "a", "text": "<input name='a' label='A'>", "extends": "b.html", "includes": []},
        "b.html": {"key": "b.html", "path": "b", "text": "<input name='b' label='B'>", "extends": "a.html", "includes": []},
    }
    cue = ps._compose_template_cue("a.html", templates)
    assert cue is not None


def test_a_missing_parent_is_not_an_error():
    templates = {"p.html": {"key": "p.html", "path": "p", "text": "<input name='p' label='P'>", "extends": "gone.html", "includes": []}}
    assert ps._compose_template_cue("p.html", templates) is not None


def test_an_unknown_key_returns_none():
    assert ps._compose_template_cue("nope.html", _templates()) is None
