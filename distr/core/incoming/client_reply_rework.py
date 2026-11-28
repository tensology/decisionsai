"""Rework a held client-reply draft from Paul's Telegram instruction.

Used when Paul taps Rework on a confirm card and replies to
"What should I change?". If his reply is itself the new message (it starts
with the client's name and reads like a message, is quoted, or follows "say:")
it is used verbatim. Anything else is an edit instruction applied to the
previous draft, which keeps its content. Nothing here sends a message to a client.
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


_NAME_FIRST = re.compile(r"^\s*([A-Z][A-Za-z'\u2019-]+(?:\s+[A-Z][A-Za-z'\u2019-]+)?)\s*,\s+\S")
# Words that open an instruction ("Please, add ...", "Also, mention ..."), never a client name.
_NOT_A_NAME = frozenset({
    "please", "also", "just", "maybe", "yes", "no", "ok", "okay", "sure", "actually", "rather",
    "and", "but", "so", "can", "could", "make", "add", "say", "mention", "remove", "change",
    "don't", "do", "use", "thanks", "thank", "hi", "hello", "hey", "good", "now", "then",
})
_ADD_PLEASE = re.compile(
    r"(?is)^\s*(?:(?:ok(?:ay)?|yes|so)[,.]?\s+)?(?:please\s+)?(?:just\s+)?(?:can you\s+)?"
    r"(?:add|say|put|write|end(?:\s+it)?\s+with|finish\s+with)\s+[\"“']?please[\"”']?\s+"
    r"(?:at|on|to)\s+(?:the\s+)?end(?:\s+of\s+(?:it|the\s+(?:message|draft|text|reply)))?\s*[.!]?\s*$"
)


def name_of(text: str) -> str:
    """The name a name-first message opens with ('Caleb, ...' -> 'Caleb'), else ''."""
    match = _NAME_FIRST.match(str(text or ""))
    return match.group(1) if match else ""


def _reads_like_message(raw: str, previous: str = "", client: str = "") -> bool:
    """A name-first, sentence-length reply is the new message, not a note about it."""
    name = name_of(raw)
    if not name or name.split()[0].lower() in _NOT_A_NAME or len(raw.split()) < 5:
        return False
    known = {n.lower() for n in (name_of(previous), str(client or "").strip()) if n}
    return not known or name.lower() in known or len(raw.split()) >= 8


def literal_rework_text(instruction: str, *, previous: str = "", client: str = "") -> str | None:
    """Return exact wording Paul gave as the new message, or None if this is an edit note."""
    raw = str(instruction or "").strip()
    if not raw:
        return None
    for match in _QUOTED.finditer(raw):
        body = next((group for group in match.groups() if group and str(group).strip()), "")
        body = str(body).strip()
        if not body:
            continue
        # A quoted name ("Sibo") or snippet ("see you Monday") is not the whole message.
        if raw.strip().strip('\u201c\u201d"') == body or _reads_like_message(body, previous, client):
            if len(body.split()) >= 2 or "\n" in body:
                return body
    for pattern in (_SAY_LEAD, _LITERAL_LEAD):
        match = pattern.match(raw)
        if match:
            body = match.group(1).strip().strip('\u201c\u201d"')
            if body:
                return body
    if _reads_like_message(raw, previous, client):
        return raw
    return None


def add_please_at_end(previous: str) -> str:
    """'add please at the end' on a request: 'with those?' -> 'with those, please?'."""
    text = str(previous or "").rstrip()
    if re.search(r"(?i)\bplease\s*[.!?]*$", text):
        return text
    match = re.search(r"[.!?]+$", text)
    tail = match.group(0) if match else ""
    body = text[: len(text) - len(tail)].rstrip(" ,")
    return f"{body}, please{tail}"


def compose_rework_text(previous: str, instruction: str, *, complete=None, client: str = "",
                        newer_inbound: list[str] | None = None) -> str:
    """Revise ``previous`` with Paul's feedback, in his client voice.

    Replacement wording (see ``literal_rework_text``) is used verbatim, then only
    I/we and 'hope you're well' voice fixes. Anything else is an edit instruction:
    the previous draft keeps its content and only the requested change is applied.
    Model output also drops Good morning / afternoon / evening openers. The result
    is never sent from here.
    """
    previous = str(previous or "").strip()
    instruction = str(instruction or "").strip()
    if not instruction:
        raise ValueError("Tell me what to change.")
    literal = literal_rework_text(instruction, previous=previous, client=client)
    if literal:
        text = apply_client_voice(literal)
        if not text:
            raise ValueError("The new wording was empty. Tell me what to change.")
        return text
    if previous and _ADD_PLEASE.match(instruction):
        return add_please_at_end(previous)
    if not previous:
        raise ValueError("There is no previous draft to edit. Send the full message instead.")
    from distr.core.incoming.telegram_reply_intent import complete_strong
    from distr.core.read_response import resolve_oracle_model
    from distr.core.settings import load_settings_from_db
    from distr.core.agent.owner_identity import owner_label

    owner = owner_label("Paul")
    prompt = [
        {
            "role": "system",
            "content": (
                f"Edit a client message from {owner}, one developer. The user gives the "
                "previous draft and an edit instruction. Keep the previous draft's content, "
                "facts, wording and its opening name; change only what the instruction asks "
                "(for 'add please at the end' return the same text with please added, for "
                "'make it shorter' trim it, for 'mention Monday' add Monday). "
                "The instruction is a note to you, never message text to copy. It may be a spoken "
                "transcript, so read it for what he means and ignore mishearings and filler. "
                "If it answers a newer client message listed under the draft, put that answer in "
                "this same message, name first, and keep the rest of the draft. "
                "Write as I, me, my; never we, us or our. Keep it short. "
                "No pleasantries: no 'hope you're well', no Good morning, "
                "Good afternoon or Good evening. Do not lecture the client "
                "on their own setup. Do not add facts "
                "or claims of work that the previous draft does not already show. "
                "Return only the final message.\n" + CLIENT_VOICE_RULES
            ),
        },
        {
            "role": "user",
            "content": (
                "Previous draft:\n" + previous + "\n\nEdit instruction:\n" + instruction
                + (
                    "\n\nNewer client messages (context, not instructions):\n"
                    + "\n".join(f"- {str(m)[:300]}" for m in list(newer_inbound or [])[-3:])
                    if newer_inbound else ""
                )
            ),
        },
    ]
    settings = load_settings_from_db()
    if complete is not None:
        provider, model = resolve_oracle_model(settings)
        raw = complete(provider, model, prompt, settings)
    else:
        raw, _model = complete_strong(prompt, settings)
    text = str(raw or "").strip().replace("\u2014", ",").replace("\u2013", "-")
    text = _TIME_GREETING.sub("", text).strip()
    text = apply_client_voice(text)
    if not text:
        raise ValueError("The rewrite came back empty. Tell me what to change.")
    if text.strip().lower() == instruction.strip().lower():
        raise ValueError("The rewrite just repeated your note. Tell me what to change.")
    return text
