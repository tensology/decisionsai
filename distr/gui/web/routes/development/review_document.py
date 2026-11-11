"""Package checked-in Development UI for App View's opaque-origin review frames."""
import base64
from pathlib import Path
import re


def development_review_document(title: str, *, styles: tuple[str, ...], scripts: tuple[str, ...], media: bool = False) -> str:
    static = Path(__file__).resolve().parents[2] / "static"
    dev = static / "development"
    fonts = (static / "vendor" / "quicksand.css").read_text(encoding="utf-8")
    fonts = re.sub(r"/static/vendor/fonts/([A-Za-z0-9_.-]+\.ttf)", lambda match:
                   "data:font/ttf;base64," + base64.b64encode(
                       (static / "vendor" / "fonts" / match[1]).read_bytes(),
                   ).decode("ascii"), fonts)
    css = fonts + "\n" + "\n".join((dev / path).read_text(encoding="utf-8") for path in (
        "shell.css", "shared/components.css", *styles,
    ))
    js = "\n".join((dev / path).read_text(encoding="utf-8") for path in scripts).replace("</script", r"<\/script")
    media_policy = " connect-src blob:; img-src blob:; media-src blob:; frame-src blob:;" if media else ""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; form-action 'none'; base-uri 'none';{media_policy}">
<title>{title}</title>
<style>body{{margin:0;color:#ececf1;font-family:Quicksand,sans-serif}}{css}</style>
<body><script type="module">{js}</script></body></html>"""
