"""Negation- and scope-aware reading of "read-only" and "deploy" in instructions.

A note such as "the v2 repo is read-only" or "keep production read-only" limits
one thing; it does not turn the whole run into a review. "Don't commit, push or
deploy" forbids a deploy; it does not ask for one. These helpers keep that
distinction in one place for intake, the Development harness and workflows.
"""

from __future__ import annotations

import re

_READ_ONLY = re.compile(r"read[ -]?only", re.I)
_NEGATED_BEFORE = re.compile(
    r"\b(?:not|isn't|isnt|aren't|no longer|never|without being)\s+(?:\w+\s+){0,1}$", re.I
)
# Run-wide wording always wins: the user is describing this run.
_RUN_WIDE = re.compile(
    r"\b(?:strictly read[ -]?only|(?:audit|review)[ -]only|"
    r"read[ -]?only (?:audit|review|investigation|inspection|pass|run|check|mode|task|turn|analysis)|"
    r"(?:work|stay|do (?:this|it)|run (?:this|it)|keep (?:it|this|everything)|be) read[ -]?only|"
    r"(?:this|it) (?:is|stays|must be|should be) read[ -]?only)\b",
    re.I,
)
_SELF = r"(?:it|this|that|everything|all|the (?:run|turn|task|work|thread))"
# "keep production read-only", "leave the v2 repo read-only"
_SCOPED_OBJECT = re.compile(
    rf"\b(?:keep|leave|treat|mount|open|use)\s+(?!{_SELF}\b)(?:[^\s.;]+\s+){{1,6}}?read[ -]?only", re.I
)
# "<thing> is read-only", "<path> - read-only", "<thing> (read-only)"
_SCOPED_SUBJECT = re.compile(
    rf"(?<!\bthis)(?<!\bit)\s*(?:\bis|\bare|\bstays?|\bremains?|\bmust be|\bshould be|-|–|—|\(|:)\s*read[ -]?only\b",
    re.I,
)
_SCOPED_TAIL = re.compile(
    r"read[ -]?only\s+(?:access|connection|user|replica|copy|reference|mount|credentials?|"
    r"(?:on|to|for|against|in|from)\s+(?!this\b|the project\b|it\b))",
    re.I,
)
_SCOPED_TARGET = re.compile(
    r"(?:\bproduction\b|\bprod\b|\blive\b|\bserver\b|\bdatabase\b|\bdb\b|\blegacy\b|\breference\b|"
    r"\bupstream\b|\bremote\b|\brepo(?:sitory)?\b|[~./][\w.-]*/|\b\w+\.(?:py|js|ts|json|com|co\.za)\b)",
    re.I,
)


def _clauses(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.;!?])\s+|\n+", str(text or "")) if part.strip()]


def _occurrence_is_run_wide(clause: str, start: int) -> bool:
    before = clause[:start]
    if _NEGATED_BEFORE.search(before):
        return False
    window = clause[max(0, start - 80): start + 60]
    if _SCOPED_OBJECT.search(window) or _SCOPED_TAIL.search(clause[start:start + 60]):
        return False
    subject = _SCOPED_SUBJECT.search(clause[max(0, start - 4): start + 10])
    if subject and before.strip() and not re.search(rf"\b{_SELF}\s*$", before.strip(" -–—(:"), re.I):
        # A named thing is read-only, not the run.
        return False
    if _SCOPED_TARGET.search(before[-80:]) and re.search(r"\b(?:keep|leave|treat|stays?|remains?|is|are)\b", before[-80:], re.I):
        return False
    return True


def requests_read_only_run(text: str) -> bool:
    """True only when the instruction makes the *whole run* read-only."""
    value = re.sub(r"[ \t]+", " ", str(text or ""))
    if not value.strip():
        return False
    for clause in _clauses(value):
        for match in _RUN_WIDE.finditer(clause):
            if not _NEGATED_BEFORE.search(clause[: match.start()]):
                return True
        for match in _READ_ONLY.finditer(clause):
            if _occurrence_is_run_wide(clause, match.start()):
                return True
    return False


# "do not commit, push or deploy", "don't deploy", "without deploying"
DEPLOY_NEGATION = re.compile(
    r"\b(?:do not|don't|dont|never|without|no|not|before|nothing)\s+(?:[\w']+,?\s+){0,5}?"
    r"(?:deploy\w*|releas\w*|promot\w*|go(?:ing)? live|push\w* (?:it |this )?live)",
    re.I,
)


def strip_negated_deploys(text: str) -> str:
    return DEPLOY_NEGATION.sub(" ", str(text or ""))
