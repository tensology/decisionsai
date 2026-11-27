"""Paul's client voice: one developer, short, no lecture on the client's setup.

Used by every client-facing draft (proposal replies, workflow replies, Jira
updates). Drafts speak as Paul ("I", never "we"), stay short, never explain a
client's own network or setup to them, and never claim work that the evidence
does not show (reproduced, tested, fixed, deployed).
"""

from __future__ import annotations

import re

CLIENT_VOICE_RULES = (
    "Write as Paul, one developer: I, me, my. Never we, us or our. "
    "Keep it short and straight to the point: a greeting and two or three plain sentences. "
    "No pleasantries such as 'hope you're well' or 'hope you're doing well'; Paul talks to these clients every day. "
    "Never explain the client's own network, Wi-Fi, VPN, firewall, devices or setup to them. "
    "Only say you reproduced, tested, fixed, verified or deployed something when the evidence shows it; "
    "otherwise say what you are doing next."
)

_WE_SWAPS: tuple[tuple[str, str], ...] = (
    (r"\bwe've\b", "I've"),
    (r"\bwe have\b", "I have"),
    (r"\bwe're\b", "I'm"),
    (r"\bwe are\b", "I am"),
    (r"\bwe'll\b", "I'll"),
    (r"\bwe will\b", "I will"),
    (r"\bwe'd\b", "I'd"),
    (r"\bwe were\b", "I was"),
    (r"\bourselves\b", "myself"),
    (r"\bours\b", "mine"),
    (r"\bour\b", "my"),
    (r"\blet us\b", "let me"),
    (r"\bwe\b", "I"),
)
_US_OBJECT = re.compile(r"\b(to|with|from|for|let|give|send|tell|ask|call|email|message|ping)\s+us\b", re.I)

_CLIENT_SETUP = re.compile(
    r"\byour (?:own )?(?:office |home |internal |company |local )?"
    r"(?:network|wi-?fi|router|firewall|vpn|proxy|dns|isp|internet connection|setup|infrastructure|"
    r"device settings|phone settings|it team|it department)\b",
    re.I,
)

UNVERIFIED_CLAIM_PATTERNS: tuple[str, ...] = (
    r"\b(?:i|we)(?:'ve| have)? (?:now |also |already )?(?:reproduced|replicated)\b",
    r"\b(?:i|we)(?:'ve| have)? (?:now |also |already )?(?:tested|verified|confirmed) (?:it|this|that|the fix)\b",
    r"\b(?:i|we)(?:'ve| have)? (?:now |also |already )?fixed\b",
)


def _match_case(source: str, replacement: str) -> str:
    if source[:1].isupper() and not replacement[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


_PLEASANTRY = re.compile(
    r"(?:\b(?:i |we )?(?:hope|trust) (?:you(?:'re| are)|you all are|all is|everything is|this finds you)"
    r"(?: (?:doing|keeping))? (?:well|good|great|fine)(?: today)?|"
    r"\b(?:i |we )?hope you(?:'re| are)? (?:having|had) a (?:good|great|lovely|nice) (?:day|week|weekend|morning)|"
    r"\bhope (?:the|your) (?:week|day) is going well)[^.!?\n]*[.!?]?[ \t]*\n?",
    re.I,
)


def drop_pleasantries(text: str) -> str:
    """Remove 'hope you're well' style openers; replies go straight to the point."""
    out = _PLEASANTRY.sub("", str(text or ""))
    out = re.sub(r",[ \t]*(?=[A-Z])", ", ", out)
    out = re.sub(r"^(\s*(?:Hi|Hey|Hello|Good (?:morning|afternoon|evening))\b[^,\n]*,)\s*,", r"\1", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def first_person(text: str) -> str:
    """Swap developer "we/us/our" for "I/me/my"."""
    out = str(text or "")
    out = _US_OBJECT.sub(lambda m: f"{m.group(1)} me", out)
    for pattern, replacement in _WE_SWAPS:
        out = re.sub(pattern, lambda m, r=replacement: _match_case(m.group(0), r), out, flags=re.I)
    return out


def drop_client_setup_lectures(text: str) -> str:
    """Remove sentences that explain the client's own network or setup."""
    value = str(text or "")
    if not _CLIENT_SETUP.search(value):
        return value
    pieces = re.split(r"((?<=[.!?])\s+|\n+)", value)
    out = []
    for index in range(0, len(pieces), 2):
        sentence = pieces[index]
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        if not _CLIENT_SETUP.search(sentence):
            out.append(sentence + separator)
    kept = "".join(out).strip()
    return kept or value


def unverified_claims(text: str, evidence: str = "") -> list[str]:
    """Claims of reproduced/tested/fixed work that the evidence does not mention."""
    proof = str(evidence or "").lower()
    found: list[str] = []
    for pattern in UNVERIFIED_CLAIM_PATTERNS:
        for match in re.finditer(pattern, str(text or ""), flags=re.I):
            verb = re.findall(r"(reproduc|replicat|test|verif|confirm|fix)", match.group(0).lower())
            stem = verb[-1] if verb else ""
            if not stem or stem not in proof:
                found.append(match.group(0).strip())
    return found


def apply_client_voice(text: str) -> str:
    return drop_pleasantries(drop_client_setup_lectures(first_person(text))).strip()
