"""Calendar tool for the Development agent: Google Calendar and iCloud (CalDAV).

provider=auto (default, or setting dev_calendar_provider): Google when a Google
account is linked, else iCloud when an Apple ID + app-specific password are
configured. Reads run freely; create / update / delete go through the
user-approval gate (dev_approvals.gate). Never reads out or prints credentials.

EventKit (macOS Calendar database) is used only inside the app process
(DECISIONS_IN_APP_PROCESS=1) and only when access is already granted; it never
asks for permission, so a background run can never hang on a prompt.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from distr.core.workflow import dev_approvals

DEFAULT_TZ = "Africa/Johannesburg"
WRITE_ACTIONS = {"create", "update", "delete"}
ACTIONS = ["list", "get", "create", "update", "delete"]
ICLOUD_CALDAV_URL = "https://caldav.icloud.com/"

SCHEMA = {
    "description": (
        "Calendar (Google or iCloud). Actions: list, get, create, update, delete. provider: auto (default: Google "
        "if linked, else iCloud), google or icloud. Times are ISO 8601; naive times are Africa/Johannesburg. "
        "create/update/delete need the user's approval in the thread: the call waits briefly, then returns "
        "needs_approval + request_id; call approval_wait, then retry the identical call with approval_id. "
        "Prefer update over delete+create."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ACTIONS},
            "provider": {"type": "string", "enum": ["auto", "google", "icloud"], "default": "auto"},
            "event_id": {"type": "string"},
            "summary": {"type": "string"},
            "start_time": {"type": "string", "description": "ISO 8601"},
            "end_time": {"type": "string", "description": "ISO 8601"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "time_zone": {"type": "string", "description": "IANA zone, default Africa/Johannesburg"},
            "time_min": {"type": "string", "description": "list: ISO start (default now)"},
            "time_max": {"type": "string", "description": "list: ISO end (default +7 days)"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
            "approval_id": {"type": "string"},
        },
        "required": ["action"],
    },
}


class NotConnected(Exception):
    pass


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


# ---------------------------------------------------------------- providers
class GoogleCalendar:
    name = "google"

    def __init__(self, connector: Any = None):
        if connector is None:
            from distr.core.agent.services.integrations.google_workspace import GoogleWorkspaceConnector
            connector = GoogleWorkspaceConnector()
        self.c = connector

    def health(self) -> tuple[bool, str]:
        try:
            return self.c.connection_health()
        except Exception as exc:
            return False, type(exc).__name__

    @staticmethod
    def _view(ev: dict[str, Any]) -> dict[str, Any]:
        out = {k: ev.get(k) for k in ("id", "summary", "start", "end", "location", "description", "htmlLink", "status")
               if ev.get(k) is not None}
        if isinstance(out.get("description"), str):
            out["description"] = out["description"][:1000]
        return out

    def list(self, tmin, tmax, n):
        evs = self.c.get_calendar_events(time_min=tmin, time_max=tmax, max_results=n)
        if evs is None:
            raise RuntimeError(getattr(self.c, "last_error", "") or "calendar read failed")
        return [self._view(e) for e in evs]

    def get(self, event_id):
        from urllib.parse import quote
        ev = self.c._make_request("GET", "https://www.googleapis.com/calendar/v3/calendars/primary/events/"
                                  + quote(str(event_id), safe=""))
        return self._view(ev) if ev else None

    def create(self, summary, start, end, description, location, tz):
        return self.c.create_calendar_event(summary, start, end, description=description, location=location, time_zone=tz)

    def update(self, event_id, summary, start, end, description, location, tz):
        return bool(self.c.update_calendar_event(event_id, summary=summary, start_time=start, end_time=end,
                                                 description=description, location=location, time_zone=tz))

    def delete(self, event_id):
        return bool(self.c.delete_calendar_event(event_id))


class ICloudCalendar:
    """iCloud over CalDAV with an app-specific password (works headless)."""
    name = "icloud"

    def __init__(self, apple_id: str, app_password: str, calendar_name: str = "", client: Any = None):
        self.apple_id, self._pw, self.calendar_name = apple_id, app_password, calendar_name
        self._client, self._cal = client, None

    def _calendar(self):
        if self._cal is not None:
            return self._cal
        if self._client is None:
            import caldav
            self._client = caldav.DAVClient(url=ICLOUD_CALDAV_URL, username=self.apple_id, password=self._pw, timeout=20)
        cals = self._client.principal().calendars()
        if self.calendar_name:
            cals = [c for c in cals if (getattr(c, "name", "") or "").strip().lower() == self.calendar_name.strip().lower()] or []
        if not cals:
            raise NotConnected(f"iCloud calendar {self.calendar_name!r} not found" if self.calendar_name else "no iCloud calendars")
        self._cal = cals[0]
        return self._cal

    def health(self) -> tuple[bool, str]:
        try:
            self._calendar()
            return True, ""
        except NotConnected as exc:
            return False, str(exc)
        except Exception as exc:  # auth errors etc.; never echo credentials
            return False, f"iCloud CalDAV login failed ({type(exc).__name__}); check the Apple ID and app-specific password"

    @staticmethod
    def _view(obj: Any) -> dict[str, Any]:
        comp = obj.icalendar_component
        def g(key):
            v = comp.get(key)
            if v is None:
                return None
            return _iso(getattr(v, "dt", v)) if hasattr(v, "dt") else str(v)
        out = {"id": g("uid"), "summary": g("summary"), "start": g("dtstart"), "end": g("dtend"),
               "location": g("location"), "description": (g("description") or "")[:1000] or None}
        return {k: v for k, v in out.items() if v is not None}

    def _find(self, uid):
        try:
            return self._calendar().event_by_uid(uid)
        except Exception:
            return None

    def list(self, tmin, tmax, n):
        evs = self._calendar().search(start=tmin, end=tmax, event=True, expand=True)
        views = [self._view(e) for e in evs]
        views.sort(key=lambda v: str(v.get("start") or ""))
        return views[:n]

    def get(self, event_id):
        ev = self._find(event_id)
        return self._view(ev) if ev else None

    def create(self, summary, start, end, description, location, tz):
        uid = f"{uuid.uuid4()}@decisions"
        props = {"uid": uid, "summary": summary, "dtstart": start, "dtend": end}
        if description:
            props["description"] = description
        if location:
            props["location"] = location
        self._calendar().save_event(**props)
        return uid

    def update(self, event_id, summary, start, end, description, location, tz):
        ev = self._find(event_id)
        if not ev:
            return False
        comp = ev.icalendar_component
        for key, val in (("summary", summary), ("dtstart", start), ("dtend", end), ("description", description),
                         ("location", location)):
            if val is not None:
                if key in comp:
                    del comp[key]
                comp.add(key, val)
        ev.save()
        return True

    def delete(self, event_id):
        ev = self._find(event_id)
        if not ev:
            return False
        ev.delete()
        return True


def eventkit_status() -> str:
    """authorized | denied | not_determined | restricted | unavailable | background. Never prompts."""
    if os.environ.get("DECISIONS_IN_APP_PROCESS") != "1":
        return "background"
    try:
        import EventKit  # type: ignore  (pyobjc-framework-EventKit, optional)
    except Exception:
        return "unavailable"
    code = int(EventKit.EKEventStore.authorizationStatusForEntityType_(0))  # 0 = EKEntityTypeEvent
    return {0: "not_determined", 1: "restricted", 2: "denied", 3: "authorized", 4: "authorized"}.get(code, "unavailable")


def _settings() -> dict[str, Any]:
    try:
        from distr.core.settings import load_settings_from_db
        return load_settings_from_db() or {}
    except Exception:
        return {}


def resolve_provider(provider: str | None, settings: dict[str, Any], *, google: Any = None, icloud: Any = None):
    choice = (provider or "auto").strip().lower()
    if choice == "auto":
        choice = str(settings.get("dev_calendar_provider") or "auto").strip().lower()
    icloud_ready = bool(str(settings.get("icloud_apple_id") or "").strip() and str(settings.get("icloud_app_password") or "").strip())

    def _icloud():
        if icloud is not None:
            return icloud
        if not icloud_ready:
            raise NotConnected("iCloud is not configured: add your Apple ID and an app-specific password in "
                               "Settings > Third party > Development agent tools")
        return ICloudCalendar(settings["icloud_apple_id"].strip(), settings["icloud_app_password"].strip(),
                              str(settings.get("icloud_calendar_name") or ""))

    if choice == "eventkit":
        status = eventkit_status()
        # EventKit read/write is not wired yet; whatever the status, prefer CalDAV and never prompt.
        if icloud is not None or icloud_ready:
            return _icloud()
        raise NotConnected(f"permission needed: macOS Calendar access is '{status}' for this process and iCloud "
                           "CalDAV is not configured; add an Apple ID + app-specific password in Settings")
    if choice == "google":
        return google or GoogleCalendar()
    if choice == "icloud":
        return _icloud()
    g = google or GoogleCalendar()
    ok, _ = g.health()
    if ok:
        return g
    if icloud is not None or icloud_ready:
        return _icloud()
    raise NotConnected("no calendar connected: link a Google account, or configure iCloud (Apple ID + app-specific password)")


# ---------------------------------------------------------------- tool
def _parse(value: Optional[str], tz: str) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))


def _res(ok: bool, summary: str, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "tool": "calendar", "summary": summary, "artifacts": [], **extra}


def calendar(action: str, provider: str | None = "auto", event_id: str | None = None, summary: str | None = None,
             start_time: str | None = None, end_time: str | None = None, description: str | None = None,
             location: str | None = None, time_zone: str | None = None, time_min: str | None = None,
             time_max: str | None = None, max_results: int | None = 10, approval_id: str | None = None,
             *, backend: Any = None, settings: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
    if action not in ACTIONS:
        return _res(False, f"unknown action {action!r}; use one of {ACTIONS}")
    tz = time_zone or DEFAULT_TZ
    try:
        ZoneInfo(tz)
        start, end = _parse(start_time, tz), _parse(end_time, tz)
        tmin, tmax = _parse(time_min, tz), _parse(time_max, tz)
    except Exception as exc:
        return _res(False, f"bad time or time_zone: {exc}")
    if action in {"get", "update", "delete"} and not event_id:
        return _res(False, f"event_id is required for {action}")
    if action == "create" and not (summary and start and end):
        return _res(False, "create needs summary, start_time and end_time")
    if start and end and end <= start:
        return _res(False, "end_time must be after start_time")

    try:
        cal = backend or resolve_provider(provider, settings if settings is not None else _settings())
        healthy, why = cal.health()
        if not healthy:
            raise NotConnected(why or "not connected")
    except NotConnected as exc:
        return _res(False, f"Calendar not connected: {exc}", error="not_connected")

    if action in WRITE_ACTIONS:
        args = {"action": action, "provider": cal.name, "event_id": event_id, "summary": summary,
                "start_time": start_time, "end_time": end_time, "description": description, "location": location,
                "time_zone": time_zone}
        proposed = {k: v for k, v in {"summary": summary, "start": start_time, "end": end_time, "description": description,
                                      "location": location, "time_zone": tz}.items() if v}
        existing = cal.get(event_id) if event_id else None
        if action in {"update", "delete"} and not existing:
            return _res(False, f"event {event_id} not found on {cal.name}")
        details = {"provider": cal.name, "action": action}
        if action == "create":
            details["after"] = proposed
        elif action == "update":
            details["before"], details["after"] = existing, proposed
        else:
            details["before"] = existing
        label = summary or (existing or {}).get("summary") or event_id
        blocked = dev_approvals.gate("calendar", action, args, approval_id,
                                     f"{action} {cal.name} event '{label}'", details)
        if blocked:
            return blocked

    try:
        if action == "list":
            tmin = tmin or datetime.now(ZoneInfo(tz))
            tmax = tmax or tmin + timedelta(days=7)
            views = cal.list(tmin, tmax, max(1, min(int(max_results or 10), 50)))
            return _res(True, f"{len(views)} {cal.name} event(s) between {tmin.isoformat()} and {tmax.isoformat()}",
                        provider=cal.name, events=views)
        if action == "get":
            ev = cal.get(event_id)
            return _res(bool(ev), f"event {event_id}" if ev else f"event {event_id} not found", provider=cal.name, event=ev)
        if action == "create":
            new_id = cal.create(summary, start, end, description, location, tz)
            return _res(bool(new_id), f"created {cal.name} event {new_id}: {summary}" if new_id else "create failed",
                        provider=cal.name, event_id=new_id)
        if action == "update":
            ok = cal.update(event_id, summary, start, end, description, location, tz)
            return _res(ok, f"updated {cal.name} event {event_id}" if ok else f"update failed for {event_id}", provider=cal.name)
        ok = cal.delete(event_id)
        return _res(ok, f"deleted {cal.name} event {event_id}" if ok else f"delete failed for {event_id}", provider=cal.name)
    except Exception as exc:
        return _res(False, f"{cal.name} calendar {action} failed: {type(exc).__name__}: {str(exc)[:200]}")
