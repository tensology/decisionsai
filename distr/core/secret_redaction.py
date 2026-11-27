"""Secret files and values: never shown to agents, threads or event logs."""

from __future__ import annotations

import re
from pathlib import Path

# Secret files never reach the model: .env*, password files, credentials, keys.
_SAFE_ENV_TEMPLATE = re.compile(r"(?i)\.(?:example|sample|template|dist|defaults?)$")
_SECRET_FILE_NAME = re.compile(
    r"(?i)^(?:\.env(?:[.-][\w.-]*)?|\.?(?:pg)?pass(?:word)?s?(?:[.-][\w.-]*)?|\.netrc|\.pgpass|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)(?:\.\w+)?|[\w.-]*\.(?:pem|key|p12|pfx|keystore|jks)|"
    r"[\w.-]*credentials?[\w.-]*|[\w.-]*(?:secret|passw(?:or)?d|-pass|_pass)[\w.-]*\.(?:txt|env|json|ya?ml|ini|cfg|conf)|"
    r"[\w.-]*(?:secret|passw(?:or)?d|-pass|_pass)[\w-]*)$"
)
_CODE_SUFFIX = re.compile(r"(?i)\.(?:py|js|jsx|ts|tsx|html?|css|scss|md|rst|go|rb|php|java|kt|swift|sql|sh)$")
_ALWAYS_SECRET_CODE = re.compile(r"(?!)")


def is_secret_file(path: str | Path) -> bool:
    name = Path(str(path)).name
    if not name or _SAFE_ENV_TEMPLATE.search(name):
        return False
    if _ALWAYS_SECRET_CODE.match(name):
        return True
    if _CODE_SUFFIX.search(name) or name.lower().startswith("test"):
        return False
    return bool(_SECRET_FILE_NAME.match(name))


_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:[\w./-]*:\d+:|\d+[:|])?\s*(?:export\s+)?[\w.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|pwd|dsn|auth)[\w.-]*\s*[:=]\s*)(\S.*)$"
)
_SECRET_JSON = re.compile(
    r"(?i)([\"'][\w.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|credential|pwd)[\w.-]*[\"']\s*:\s*)([\"'])[^\"'\n]*\2"
)
_SECRET_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@")
_SECRET_TOKEN = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{12,}|sk_(?:live|test)_[a-z0-9]{12,}|xox[baprs]-[a-z0-9-]{12,}|gh[pousr]_[a-z0-9]{12,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)


def redact_secrets(text: str) -> str:
    """Mask secret values in tool output and logs; keep the key names readable."""
    value = str(text or "")
    if not value:
        return value
    value = _SECRET_TOKEN.sub("[redacted]", value)
    value = _SECRET_URL.sub(r"\1[redacted]@", value)
    value = _SECRET_JSON.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]{m.group(2)}", value)
    return _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + "[redacted]", value)


