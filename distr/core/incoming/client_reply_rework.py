"""Rework a held client-reply draft from Paul's Telegram instruction.

Used when Paul taps Rework on a confirm card and replies to
"What should I change?". Literal wording in that instruction is kept
verbatim; otherwise the previous draft is rewritten in Paul's client voice.
Nothing here sends a message to a client.
"""

from __future__ import annotations

import re

from distr.core.client_voice import CLIENT_VOICE_RULES, apply_client_voice

# "update the message to say …" / "rather change it to: …"
_LITERAL_LEAD = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:rather\s+)?"
    r"(?:just\s+)?(?:please\s+)?"
    r"(?:update|change|rewrite|reword|replace|edit|make)\s+"
    r"(?:the\s+)?(?:message|draft|text|reply|it|this)\s+"
    r"(?:to(?:\s+say|\s+read|\s+be|\s+use)?|so\s+it\s+(?:says|reads)|as)\s*"
    r"[:\-–—]?\s*(.+)\s*$"
)
_SAY_LEAD = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:rather\s+)?(?:just\s+)?"
    r"(?:say|write|use|send)\s*[:\-–—]\s*(.+)\s*$"
)
_QUOTED = re.compile(r'(?s)```(?:\w+)?\s*\n?(.*?)```|[“"]{3}(.*?)[”"]{3}|[“"](.+?)[”"]')
_META_ONLY = re.compile(
    r"(?is)^\s*(?:please\s+)?(?:don't|do not|remove|add|mention|call (?:him|her|them)|"
    r"change the name|make it|shorter|longer|no greeting|leave out|drop|include)\b"
)
_META_START = re.compile(
    r"(?is)^\s*(?:please\s+|rather\s+)*(?:change|update|rewrite|reword|don't|do not|remove|mention)\b"
)
_TIME_GREETING = re.compile(
    r"^\s*Good (?:morning|afternoon|evening)\s*,?\s*",
    re.I,
)


def literal_rework_text(instruction: str) -> str | None:
    """Return exact wording Paul asked to use, or None if this is a rewrite note."""
    raw = str(instruction or "").strip()
    if not raw:
        return None
    for match in _QUOTED.finditer(raw):
        body = next((group for group in match.groups() if group and str(group).strip()), "")
        body = str(body).strip()
        if not body:
            continue
        # A quoted name ("Sibo") is not the whole message.
        if len(body.split()) >= 2 or "\n" in body or raw.strip().strip('“”"') == body:
            return body
    for pattern in (_SAY_LEAD, _LITERAL_LEAD):
        match = pattern.match(raw)
        if match:
            body = match.group(1).strip().strip('“”"')
            if body:
                return body
    if not _META_ONLY.match(raw) and not _META_START.match(raw) and (
        len(raw) > 20 or re.search(r"[.!?]", raw)
    ):
        return raw
    return None


def compose_rework_text(previous: str, instruction: str, *, complete=None) -> str:
    """Redraft ``previous`` using Paul's instruction, in his client voice.

    Literal text in the instruction is used verbatim (then only I/we and
    'hope you're well' voice fixes). Model output also drops Good morning
    / afternoon / evening openers. The result is never sent from here.
    """
    previous = str(previous or "").strip()
    instruction = str(instruction or "").strip()
    if not instruction:
        raise ValueError("Tell me what to change.")
    literal = literal_rework_text(instruction)
    if literal:
        text = apply_client_voice(literal)
        if not text:
            raise ValueError("The new wording was empty. Tell me what to change.")
        return text
    from distr.core.read_response import _default_complete, resolve_oracle_model
    from distr.core.settings import load_settings_from_db
    from distr.core.agent.owner_identity import owner_label

    owner = owner_label("Paul")
    prompt = [
        {
            "role": "system",
            "content": (
                f"Rewrite a client message from {owner}, one developer. "
                "Write as I, me, my; never we, us or our. Keep it short. "
                "No pleasantries: no 'hope you're well', no Good morning, "
                "Good afternoon or Good evening. Do not lecture the client "
                "on their own setup. Honour the rewrite request. If it includes "
                "the exact wording to use, use that wording. Do not add facts "
                "or claims of work that the previous draft does not already show. "
                "Return only the final message.\n" + CLIENT_VOICE_RULES
            ),
        },
        {
            "role": "user",
            "content": (
                "Previous draft:\n" + previous + "\n\nRewrite request:\n" + instruction
            ),
        },
    ]
    settings = load_settings_from_db()
    provider, model = resolve_oracle_model(settings)
    raw = (complete or _default_complete)(provider, model, prompt, settings)
    text = str(raw or "").strip().replace("\u2014", ",").replace("\u2013", "-")
    text = _TIME_GREETING.sub("", text).strip()
    text = apply_client_voice(text)
    if not text:
        raise ValueError("The rewrite came back empty. Tell me what to change.")
    return text
