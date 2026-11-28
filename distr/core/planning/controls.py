"""What a rendered page does: where its links go, where its forms post, what its buttons are wired to.

``read_controls`` runs in the capture browser (headless Firefox) right after the screenshot. The result is
saved beside the page image and feeds the flow map (``flow_map``) and each page's ticket (``blueprint``).
Only structure is kept: no field values, no cookies, no response bodies.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

_JS = Path(__file__).with_name("controls_probe.js")
_GATE_PATH = re.compile(r"/(?:log-?in|sign-?in|signin|auth|accounts/login|account/login|admin/login|session/new|sso)(?:/|$|\?)", re.I)
_MAX_CHARS = 60_000


def _route_of(url: str, origin: str, base: str) -> tuple[str, str]:
    """(kind, target): route / anchor / external / mailto / tel / script / empty."""
    value = (url or "").strip()
    if not value or value == "#":
        return "empty", ""
    low = value.lower()
    if low.startswith("javascript:"):
        return "script", ""
    if low.startswith("mailto:"):
        return "mailto", ""
    if low.startswith("tel:") or low.startswith("sms:"):
        return "tel", ""
    if value.startswith("#"):
        return "anchor", value[:60]
    absolute = urljoin(base or origin + "/", value)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return "external", ""
    if origin and parsed.netloc.lower() != urlparse(origin).netloc.lower():
        return "external", parsed.netloc.lower()
    path = parsed.path or "/"
    return "route", path + ("?…" if parsed.query else "")


def nav_info(requested: str, page, response) -> dict[str, Any]:
    """How the page was reached: the redirect chain, the final address, and whether it is behind a sign-in."""
    chain: list[dict[str, Any]] = []
    try:
        request = response.request if response is not None else None
        hops = []
        while request is not None and getattr(request, "redirected_from", None) is not None and len(hops) < 8:
            request = request.redirected_from
            hops.append(request)
        for hop in reversed(hops):
            reply = hop.response()
            chain.append({"url": urlparse(hop.url).path or "/", "status": reply.status if reply else 0})
    except Exception:
        logger.debug("controls: redirect chain unreadable", exc_info=True)
    final = urlparse(page.url or requested)
    status = int(getattr(response, "status", 0) or 0)
    asked = urlparse(requested).path or "/"
    went = final.path or "/"
    redirected = bool(chain) or went.rstrip("/") != asked.rstrip("/")
    gate = ""
    if status in (401, 403):
        gate = "denied"
    elif redirected and _GATE_PATH.search(went + ("?" + final.query if final.query else "")) and not _GATE_PATH.search(asked):
        gate = "login"
    ctype = ""
    try:
        ctype = (response.headers.get("content-type") or "").split(";")[0].strip().lower() if response is not None else ""
    except Exception:
        pass
    return {"requested": asked, "final": went, "status": status, "redirects": chain, "redirected": redirected,
            "gate": gate, "content_type": ctype}


def read_controls(page, requested: str, response=None) -> dict[str, Any]:
    """Controls of the page open in ``page`` (never raises; {} when unreadable)."""
    try:
        raw = page.evaluate(_JS.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("controls: page not read", exc_info=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    origin = "{0.scheme}://{0.netloc}".format(urlparse(requested))
    base = page.url or requested
    out: dict[str, Any] = {"title": raw.get("title") or "", "framework": raw.get("framework") or "", "refresh": raw.get("refresh") or ""}
    links = []
    for row in raw.get("links") or []:
        kind, target = _route_of(row.get("h"), origin, base)
        links.append({"text": row.get("t") or "", "kind": kind, "to": target, "in": row.get("rg") or "body",
                      **({"new_tab": 1} if row.get("tg") == "_blank" else {}), **({"hooks": row["hd"]} if row.get("hd") else {})})
    forms = []
    for row in raw.get("forms") or []:
        action = row.get("a") or ""
        kind, target = _route_of(action, origin, base) if action else ("route", (urlparse(base).path or "/"))
        forms.append({"method": (row.get("m") or "get").upper(), "action": target if kind == "route" else (action or ""),
                      "kind": kind, "in": row.get("rg") or "body", "id": row.get("id") or "", "csrf": bool(row.get("csrf")),
                      **({"multipart": 1} if row.get("enc") else {}),
                      "fields": [{"name": f.get("n") or "", "type": f.get("t") or "text", "label": f.get("l") or "", "required": bool(f.get("r")),
                                  **({"options": f["o"]} if f.get("o") else {})} for f in (row.get("fields") or [])],
                      "submit": row.get("submits") or [], **({"hooks": row["h"]} if row.get("h") else {})})
    buttons = [{"text": b.get("t") or "", "type": b.get("ty") or "button", "form": b.get("fm", -1), "in": b.get("rg") or "body",
                **({"disabled": 1} if b.get("dis") else {}), **({"hooks": b["hd"]} if b.get("hd") else {})}
               for b in (raw.get("buttons") or [])]
    loose = [{"name": f.get("n") or "", "type": f.get("t") or "text", "label": f.get("l") or "", **({"hooks": f["hd"]} if f.get("hd") else {})}
             for f in (raw.get("loose") or [])]
    out.update(links=links, forms=forms, buttons=buttons, fields=loose)
    out["nav"] = nav_info(requested, page, response)
    return _bounded(out)


def _bounded(out: dict[str, Any]) -> dict[str, Any]:
    import json

    for key in ("links", "buttons", "fields"):
        while len(json.dumps(out, separators=(",", ":"))) > _MAX_CHARS and out.get(key):
            out[key] = out[key][: max(0, len(out[key]) * 2 // 3)]
    return out


def sanitize(value: Any) -> dict[str, Any]:
    """Saved controls are untrusted input: keep only the known shape, with bounded strings."""
    if not isinstance(value, dict):
        return {}

    def s(x, n=120):
        return re.sub(r"[\x00-\x1f\x7f]", " ", str(x or ""))[:n]

    def rows(name, keys, cap):
        out = []
        for row in (value.get(name) or [])[:cap]:
            if isinstance(row, dict):
                out.append({k: (row[k] if isinstance(row.get(k), (int, bool, list, dict)) else s(row.get(k), 200)) for k in keys if k in row})
        return out

    nav = value.get("nav") if isinstance(value.get("nav"), dict) else {}
    clean = {
        "title": s(value.get("title")), "framework": s(value.get("framework"), 20),
        "links": rows("links", ("text", "kind", "to", "in", "new_tab", "hooks"), 200),
        "forms": rows("forms", ("method", "action", "kind", "in", "id", "csrf", "multipart", "fields", "submit", "hooks"), 40),
        "buttons": rows("buttons", ("text", "type", "form", "in", "disabled", "hooks"), 100),
        "fields": rows("fields", ("name", "type", "label", "hooks"), 40),
        "nav": {"requested": s(nav.get("requested")), "final": s(nav.get("final")), "status": int(nav.get("status") or 0),
                "redirects": [{"url": s(r.get("url")), "status": int(r.get("status") or 0)} for r in (nav.get("redirects") or [])[:8] if isinstance(r, dict)],
                "redirected": bool(nav.get("redirected")), "gate": s(nav.get("gate"), 12), "content_type": s(nav.get("content_type"), 60)},
    }
    return clean
