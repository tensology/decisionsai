"""Bounded CX43 monitor: two Hetzner GETs, no agent, model, or provisioning API.

State belongs to the scheduler directory. A persisted send intent and a stable
Message-ID let the next check reconcile an interrupted send without resending.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, DecimalException
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://api.hetzner.cloud/v1"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
TOKEN_FILE = Path.home() / ".config/hcloud/availability-token"
STATE_DIR = Path.home() / ".codex/automations/hetzner-cx43-availability"
LOCATIONS = {"fsn1": "Falkenstein (FSN1)", "nbg1": "Nuremberg (NBG1)"}
TIMEOUT = (3, 8)


def _save(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def _get(endpoint: str, token: str) -> dict:
    response = requests.get(
        API + endpoint,
        headers={"Authorization": f"Bearer {token}", "Cache-Control": "no-cache"},
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


def available_locations(payload: dict) -> list[str]:
    """Require the exact plan/specs and explicit current per-location availability."""
    server = next((s for s in payload["server_types"] if s.get("name") == "cx43"), None)
    if server is None:
        return []
    if not (
        server.get("cores") == 8 and server.get("memory") == 16
        and server.get("disk") == 160 and server.get("architecture") == "x86"
        and server.get("cpu_type") == "shared"
    ):
        raise ValueError("CX43 specifications do not match the requested plan")
    # Datacenter available lists were removed in October 2026. Never infer stock
    # from a price, a supported location, a recommendation, or a truthy string.
    return [
        row["name"] for row in server["locations"]
        if row.get("name") in LOCATIONS and row.get("available") is True
    ]


def alert_body(locations: list[str], payload: dict) -> str:
    pricing = payload["pricing"]
    currency = pricing["currency"]
    server = next(row for row in pricing["server_types"] if row["name"] == "cx43")
    ipv4 = next(row for row in pricing["primary_ips"] if row["type"] == "ipv4")
    backup = Decimal(str(pricing["server_backup"]["percentage"])) / 100
    if not (0 <= backup <= 1):
        raise ValueError("Invalid backup percentage")
    lines = [
        "Hetzner reports CX43 available in Germany:",
        "CX43: 8 shared x86 vCPUs, 16 GB RAM, 160 GB disk.",
    ]
    for location in locations:
        cost = Decimal(str(next(p for p in server["prices"] if p["location"] == location)["price_monthly"]["net"]))
        ip = Decimal(str(next(p for p in ipv4["prices"] if p["location"] == location)["price_monthly"]["net"]))
        if not (cost.is_finite() and ip.is_finite() and cost > 0 and ip >= 0):
            raise ValueError("Invalid server or IPv4 price")
        total = (cost + ip) * Decimal("1.15")
        with_backup = (cost * (1 + backup) + ip) * Decimal("1.15")
        lines.append(
            f"{LOCATIONS[location]}: server {currency} {cost:.2f}/month + "
            f"IPv4 {currency} {ip:.2f}/month, excluding VAT. "
            f"Estimated total with 15% South African VAT: {currency} {total:.2f}/month; "
            f"including backups ({backup * 100:g}% of server price): {currency} {with_backup:.2f}/month."
        )
    lines.extend([
        "Prices use the live API's billing currency; bank conversion fees are excluded.",
        "Order in Hetzner Console: https://console.hetzner.com/projects "
        "(select your project, Add server, CX43, and one of the locations above).",
        "Stock can change before checkout. Nothing has been ordered or reserved.",
        "Source: https://docs.hetzner.cloud/reference/cloud#server-types",
        "Checked: " + datetime.now(timezone.utc).isoformat(),
    ])
    return "\n\n".join(lines)


def _connect_mail():
    from distr.core.automation.gmail_readonly import connect

    return connect()


def _pause(automation_id: str) -> None:
    from distr.core.automation.store import notify_automation_data_changed, update_automation

    update_automation(automation_id, status="paused")
    notify_automation_data_changed()


def _mail_request(mail, method: str, url: str, **kwargs) -> dict:
    # Keep the HTTP status so a definite auth rejection can retry after reconnect.
    # The shared connector's generic request method collapses all errors to None.
    response = requests.request(
        method, url, headers={"Authorization": f"Bearer {mail.access_token}"},
        timeout=TIMEOUT, allow_redirects=False, **kwargs,
    )
    response.raise_for_status()
    return response.json()


class HetznerAvailabilityTool:
    def _run(
        self, *, automation_id: str, recipient: str,
        token_file: str | None = None, state_dir: str | None = None, **_: object,
    ) -> dict:
        import fcntl

        folder = Path(state_dir).expanduser() if state_dir else STATE_DIR
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (folder / "check.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return self._result("A Hetzner check is already running.", "busy")
            path = folder / "state.json"
            # Corrupt state must never reset the duplicate-send protection.
            state = json.loads(path.read_text()) if path.exists() else {}
            result = self._check(path, state, automation_id, recipient, token_file)
            _save(path, state)
            return result

    @staticmethod
    def _result(summary: str, status: str, **extra: object) -> dict:
        return {"success": True, "notify": False, "status": status, "markdown": summary, **extra}

    def _mail_action(self, state: dict, detail: str) -> dict:
        first = not state.get("mail_action_notified")
        state["mail_action_notified"] = True
        summary = "Hetzner alert needs attention: " + detail
        return self._result(summary, "mail_action_required", notify=first, needs_action=True, headline=summary)

    def _check(self, path: Path, state: dict, automation_id: str, recipient: str, token_file: str | None) -> dict:
        state["checked_at"] = datetime.now(timezone.utc).isoformat()
        if state.get("sent_message_id"):
            _pause(automation_id)
            return self._result("Hetzner availability email already sent; automation paused.", "sent")
        # Reconcile a send with an uncertain outcome even if stock disappeared.
        if state.get("send_pending"):
            try:
                mail = _connect_mail()
                listing = _mail_request(
                    mail, "GET", GMAIL + "?maxResults=1&q=" + quote("in:sent rfc822msgid:" + state["message_id"]),
                )
                if listing and listing.get("messages"):
                    state["sent_message_id"] = listing["messages"][0]["id"]
                    _save(path, state)
                    _pause(automation_id)
                    return self._result("Confirmed previous Hetzner email delivery; automation paused.", "sent")
            except Exception:
                pass
            return self._mail_action(state, "Check Gmail Sent for the Hetzner CX43 alert. A previous send has an uncertain outcome; automatic resending is disabled to prevent duplicates.")

        token_path = Path(token_file).expanduser() if token_file else TOKEN_FILE
        token = os.environ.get("HCLOUD_TOKEN", "").strip()
        if not token and token_path.exists():
            token = token_path.read_text().strip()
        if not token:
            return self._result("Availability unconfirmed: save a read-only Hetzner API token in " + str(token_path), "token_required")
        try:
            locations = available_locations(_get("/server_types?name=cx43", token))
            if not locations:
                return self._result("CX43 is not reported available in FSN1 or NBG1.", "unavailable")
            body = alert_body(locations, _get("/pricing", token))
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, StopIteration, DecimalException):
            # Do not log request headers, tokens, or unknown error payloads.
            return self._result("Availability or pricing could not be confirmed from Hetzner; no alert sent.", "unconfirmed")

        try:
            mail = _connect_mail()
        except Exception:
            return self._mail_action(state, "Reconnect Google with Gmail access in DecisionsAI Settings > Connected accounts, then let the next scheduled check retry.")
        message_id = hashlib.sha256(automation_id.encode()).hexdigest()[:24] + "@decisionsai.local"
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = "Hetzner CX43 available in Germany"
        message["Message-ID"] = "<" + message_id + ">"
        message.set_content(body)
        state.update(send_pending=True, message_id=message_id)
        _save(path, state)  # write intent BEFORE the external side effect
        try:
            response = _mail_request(
                mail, "POST", GMAIL + "/send",
                json={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()},
            )
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in {400, 401, 403, 404}:
                state.pop("send_pending", None)  # Gmail explicitly rejected it, no delivery
                return self._mail_action(state, "Gmail rejected the alert. Reconnect Google with Gmail send permission in DecisionsAI Settings > Connected accounts; the next check will retry.")
            response = None
        except Exception:
            response = None
        if not isinstance(response, dict) or not response.get("id"):
            return self._mail_action(state, "Check Gmail Sent and Google connection permissions. The send outcome is uncertain; the next check will reconcile it without sending a duplicate.")
        state["sent_message_id"] = response["id"]
        _save(path, state)  # persist success even if pausing fails
        _pause(automation_id)
        return self._result("Hetzner CX43 availability email sent; automation paused.", "sent")
