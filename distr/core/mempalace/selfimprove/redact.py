"""Redact secrets and personal data before anything is filed (on by default)."""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED:private-key]"),
    (re.compile(r"\b(sk|rk|pk)-(?:proj-|live-|test-|ant-)?[A-Za-z0-9_\-]{16,}\b"), "[REDACTED:api-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED:github-token]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED:github-token]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"), "[REDACTED:slack-token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED:aws-key]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"), "[REDACTED:google-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"), "[REDACTED:jwt]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-~+/=]{12,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|auth)\b(\s*[:=]\s*|\s+is\s+)(['\"]?)[^\s'\"]{4,}\3"),
     r"\1\2\3[REDACTED]\3"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s:/@]+:[^\s@/]+@"), r"\1[REDACTED]@"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED:email]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[REDACTED:number]"),
    (re.compile(r"(?<!\w)\+?\d{1,3}[ -]?\(?\d{2,3}\)?[ -]?\d{3}[ -]?\d{4}\b"), "[REDACTED:phone]"),
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "[REDACTED:hex]"),
    (re.compile(r"\b[A-Za-z0-9+/_\-]{48,}={0,2}(?![A-Za-z0-9])"), "[REDACTED:blob]"),
]


def redact(text: str, *, enabled: bool = True) -> str:
    if not enabled or not text:
        return text or ""
    out = text
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    return out
