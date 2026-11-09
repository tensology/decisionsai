"""Robust sentence extraction for streaming TTS buffers."""

from __future__ import annotations

import re


_PLACEHOLDER = "\x00DOT\x00"
_DECIMAL_RE = re.compile(r"(\d)\.(\d)")
_ABBREV_RE = re.compile(
    r"\b(?:"
    r"Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|No|Vol|Ch|Fig|Sec|Art|Ref|"
    r"Ave|Blvd|Dept|Est|Ltd|Inc|Corp|Co|"
    r"vs|etc|cf|approx|ibid|"
    r"e\.g|i\.e|op\.cit|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    r")\.",
    re.IGNORECASE,
)
# Hostnames / domain-like project folder names ("merrypak.co.za", "site.co.today").
# Protect every internal dot so sentence splitting cannot treat them as stops and
# drop the leading labels (heard as only ".co.za"). Require 2+ dots or a common
# lowercase TLD so "immediately.Gamma" stays a real sentence boundary.
_DOMAIN_LIKE_COMMON_TLDS = (
    "com|net|org|io|ai|dev|app|co|za|uk|us|info|biz|today|xyz|me|tv|cc|cloud|tech"
)
_DOMAIN_LIKE_RE = re.compile(
    rf"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{{0,61}}[A-Za-z0-9])?\.){{2,}}[a-z]{{2,24}}\b"
    rf"|\b[A-Za-z0-9](?:[A-Za-z0-9-]{{0,61}}[A-Za-z0-9])?\.(?:{_DOMAIN_LIKE_COMMON_TLDS})\b"
)
_WORD_RE = re.compile(r"\b[\w']+\b")
_SENTENCE_BOUNDARY_RE = re.compile(
    r"([^.!?]*\w[^.!?]*[.!?]+[\"'”’)\]]*)(\s+|$|(?=[A-Z0-9]))"
)


def spoken_word_count(text: str) -> int:
    """Count spoken words for deciding whether a TTS chunk has enough weight."""
    return len(_WORD_RE.findall(text or ""))


def tts_chunk_has_enough_weight(
    sentences: list[str],
    *,
    min_words: int = 80,
    min_chars: int = 360,
    max_sentences: int = 3,
) -> bool:
    """Return True when a sentence group is substantial enough to synthesize.

    For live playback, short sentence-by-sentence synthesis sounds chopped up.
    Prefer at least three complete sentences, while still allowing very long
    text to flush before it becomes an oversized provider request.
    """
    if not sentences:
        return False
    text = " ".join(s.strip() for s in sentences if s.strip())
    if not text:
        return False
    return (
        spoken_word_count(text) >= min_words
        or len(text) >= min_chars
        or len(sentences) >= max_sentences
    )


def extract_complete_sentences(text: str) -> tuple[list[str], str]:
    """Extract complete sentences from a streaming text buffer.

    The live TTS providers receive arbitrary deltas, so sentence boundaries can
    arrive around decimals, abbreviations, domain-like names, markdown cleanup,
    or punctuation with no following whitespace. This splitter protects common
    non-boundary dots and keeps any text before a mid-buffer match so leading
    labels of hostnames are never dropped.
    """
    if not text:
        return [], ""

    protected = text
    while True:
        replaced = _DECIMAL_RE.sub(
            lambda match: match.group(1) + _PLACEHOLDER + match.group(2),
            protected,
        )
        if replaced == protected:
            break
        protected = replaced

    # Protect hostnames before abbreviations: otherwise \bCo. inside
    # "merrypak.co.za" is treated as the company abbreviation and only the
    # trailing ".co.za" fragment survives as a spoken sentence.
    protected = _DOMAIN_LIKE_RE.sub(
        lambda match: match.group(0).replace(".", _PLACEHOLDER),
        protected,
    )

    protected = _ABBREV_RE.sub(
        lambda match: match.group(0).replace(".", _PLACEHOLDER),
        protected,
    )

    sentences_raw: list[str] = []
    pos = 0
    while pos < len(protected):
        match = _SENTENCE_BOUNDARY_RE.search(protected[pos:])
        if not match:
            break
        # search() can match after the current cursor (false boundary dots). Keep
        # the skipped prefix so "Project merrypak.co.za started." is not reduced
        # to "co.za started."
        absolute_end = pos + match.end()
        sentence = (protected[pos:pos + match.start()] + match.group(1)).strip()
        if absolute_end >= len(protected) and re.search(
            rf"\b[vV]?\d+(?:{re.escape(_PLACEHOLDER)}\d+)*\.$",
            sentence,
        ):
            break
        if len(sentence.replace(_PLACEHOLDER, "")) >= 2:
            sentences_raw.append(sentence)
        pos = absolute_end

    remaining = protected[pos:]

    def restore(value: str) -> str:
        return value.replace(_PLACEHOLDER, ".")

    return [restore(sentence) for sentence in sentences_raw], restore(remaining)


def is_redundant_sentence(
    normalized_sentence: str,
    processed_sentences: set[str],
    *,
    min_subset_chars: int = 20,
) -> bool:
    """Return True only for deterministic duplicate/redundant sentence cases.

    Streaming providers can resend the same sentence or emit a shorter partial
    sentence followed by a longer final version. The old word-overlap heuristic
    skipped legitimate sentences that merely shared wording, which made live TTS
    sound like it had dropped whole sentences. Keep this exact/subset-only.
    """
    if normalized_sentence in processed_sentences:
        return True
    if len(normalized_sentence) <= min_subset_chars:
        return False
    for processed in processed_sentences:
        if len(processed) > min_subset_chars and normalized_sentence in processed:
            return True
    return False
