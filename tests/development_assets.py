"""Read the composed Development surface for existing asset contract checks."""
from pathlib import Path
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]

def development_template():
    environment = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block head_styles %}{% endblock %}{% block content %}{% endblock %}{% block body_scripts %}{% endblock %}"}),
        FileSystemLoader(ROOT / "distr/gui/web/templates"),
    ]))
    return environment.get_template("workflows/studio.html").render()

def development_assets(suffix):
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "distr/gui/web/static/development").rglob("*" + suffix)))
