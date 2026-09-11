"""Lossless translation of the schedule subset supported by Decisions."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from distr.core.automation.store import normalize_schedule

DAYS = {"SU": "0", "MO": "1", "TU": "2", "WE": "3", "TH": "4", "FR": "5", "SA": "6"}

def integer(value, low, high, label):
    text = str(value)
    if not text.isdigit() or not low <= int(text) <= high:
        raise ValueError(f"Unsupported {label}: {text}")
    return int(text)

def zone(name):
    if name:
        try:
            ZoneInfo(name)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Unknown schedule timezone: {name}") from exc
    return name

def schedule_from_rrule(value):
    raw = str(value or "").strip()
    lines = raw.splitlines()
    starts = [line for line in lines if line.upper().startswith("DTSTART")]
    rules = [line for line in lines if line.upper().startswith("RRULE:")]
    if len(starts) > 1 or len(rules) > 1 or any(line.upper().startswith(("RDATE", "EXDATE", "EXRULE")) for line in lines):
        raise ValueError("Multiple recurrence rules and exclusions cannot be imported faithfully.")
    start = None
    timezone_name = ""
    if starts:
        match = re.fullmatch(r"DTSTART(?:;TZID=([^:;]+))?:(\d{8}T\d{6})(Z)?", starts[0], re.I)
        if not match or (match[1] and match[3]):
            raise ValueError("Unsupported DTSTART. Use an explicit local time and timezone, or UTC.")
        timezone_name = zone(match[1] or ("UTC" if match[3] else ""))
        start = datetime.strptime(match[2], "%Y%m%dT%H%M%S")
        aware = start.replace(tzinfo=ZoneInfo(timezone_name)) if timezone_name else start.astimezone()
        if aware > datetime.now(timezone.utc):
            raise ValueError("Future DTSTART cannot be preserved by this importer.")
        if start.second:
            raise ValueError("Second-level recurrence offsets cannot be preserved.")
    rule = rules[0][6:] if rules else raw
    pairs = {}
    for token in rule.split(";"):
        if "=" not in token:
            raise ValueError("Invalid recurrence rule.")
        key, val = token.split("=", 1)
        key = key.strip().upper()
        if key in pairs:
            raise ValueError(f"Duplicate recurrence field: {key}")
        pairs[key] = val.strip().upper()
    supported = {"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE", "BYSECOND", "BYDAY", "BYMONTHDAY", "WKST"}
    unsupported = set(pairs) - supported
    if unsupported:
        raise ValueError("Unsupported recurrence fields: " + ", ".join(sorted(unsupported)))
    if pairs.get("INTERVAL", "1") != "1":
        raise ValueError("Recurring intervals greater than one cannot be preserved by this importer.")
    if pairs.get("BYSECOND", "0") != "0":
        raise ValueError("Only recurrence at second zero is supported.")
    if "WKST" in pairs and pairs["WKST"] not in DAYS:
        raise ValueError("Invalid recurrence week start.")
    freq = pairs.get("FREQ", "")
    hour = integer(pairs.get("BYHOUR", start.hour if start else 9), 0, 23, "recurrence hour")
    minute = integer(pairs.get("BYMINUTE", start.minute if start else 0), 0, 59, "recurrence minute")
    result = {"time": f"{hour:02}:{minute:02}", "timezone": timezone_name}
    if freq == "HOURLY":
        if minute or any(key in pairs for key in ("BYHOUR", "BYDAY", "BYMONTHDAY")):
            raise ValueError("Only hourly recurrence at minute zero is supported.")
        result["kind"] = "hourly"
    elif freq == "DAILY" and "BYMONTHDAY" not in pairs:
        result["kind"] = "weekly" if "BYDAY" in pairs else "daily"
    elif freq == "WEEKLY" and "BYMONTHDAY" not in pairs:
        result["kind"] = "weekly"
    elif freq == "MONTHLY" and "BYDAY" not in pairs:
        result.update(kind="monthly", days=str(integer(pairs.get("BYMONTHDAY", start.day if start else 1), 1, 31, "month day")))
    else:
        raise ValueError(f"Unsupported recurrence combination: {freq}")
    if result["kind"] == "weekly":
        default = list(DAYS)[(start.weekday() + 1) % 7] if start else "MO"
        selected = pairs.get("BYDAY", default).split(",")
        if any(day not in DAYS for day in selected):
            raise ValueError("Ordinal and unknown weekdays cannot be preserved.")
        result["days"] = ",".join(DAYS[day] for day in selected)
    return normalize_schedule(result, strict=True)

def import_schedule(data):
    if isinstance(data.get("schedule"), dict):
        value = dict(data["schedule"])
        unsupported = set(value) - {"kind", "frequency", "time", "run_at", "interval", "interval_value", "interval_unit", "days", "schedule_days", "timezone"}
        if unsupported:
            raise ValueError("Unsupported schedule fields: " + ", ".join(sorted(unsupported)))
        zone(str(value.get("timezone") or ""))
        return normalize_schedule(value, strict=True)
    if data.get("rrule"):
        return schedule_from_rrule(data["rrule"])
    cron = str(data.get("cron") or "").strip()
    if cron:
        fields = cron.split()
        if len(fields) != 5:
            raise ValueError("Only five-field cron schedules can be imported.")
        minute, hour, day, month, weekday = fields
        result = {"time": f"{integer(hour, 0, 23, 'cron hour'):02}:{integer(minute, 0, 59, 'cron minute'):02}", "timezone": zone(str(data.get("timezone") or ""))}
        if month != "*" or (day != "*" and weekday != "*"):
            raise ValueError("Cron month constraints or combined day constraints cannot be preserved.")
        if day != "*":
            result.update(kind="monthly", days=str(integer(day, 1, 31, "cron month day")))
        elif weekday != "*":
            result.update(kind="weekly", days=",".join(str(integer(x, 0, 7, "cron weekday") % 7) for x in weekday.split(",")))
        else:
            result["kind"] = "daily"
        return normalize_schedule(result, strict=True)
    return normalize_schedule({"kind": "daily", "time": data.get("time") or "09:00", "timezone": zone(str(data.get("timezone") or ""))}, strict=True)
