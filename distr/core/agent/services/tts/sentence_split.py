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
_WORD_RE = re.compile(r"\b[\w']+\b")


def spoken_word_count(text: str) -> int:
    """Count spoken words for deciding whether a TTS chunk has enough weight."""
    return len(_WORD_RE.findall(text or ""))


def tts_chunk_has_enough_weight(
    sentences: list[str],
    *,
    min_words: int = 14,
    min_chars: int = 72,
    max_sentences: int = 3,
) -> bool:
    """Return True when a sentence group is substantial enough to synthesize."""
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
    arrive around decimals, abbreviations, markdown cleanup, or punctuation with
    no following whitespace. This splitter protects common non-boundary dots and
    advances by match end position, which prevents skipped/duplicated fragments
    when a sentence match starts after a protected or stray punctuation mark.
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

    protected = _ABBREV_RE.sub(
        lambda match: match.group(0).replace(".", _PLACEHOLDER),
        protected,
    )

    sentences_raw: list[str] = []
    pos = 0
    while pos < len(protected):
        match = re.search(r"([^.!?]*\w[^.!?]*[.!?]+)(\s+|$|(?=[A-Z0-9]))", protected[pos:])
        if not match:
            break
        sentence = match.group(1).strip()
        if pos + match.end() >= len(protected) and re.search(
            rf"\b[vV]?\d+(?:{re.escape(_PLACEHOLDER)}\d+)*\.$",
            sentence,
        ):
            break
        if len(sentence.replace(_PLACEHOLDER, "")) >= 2:
            sentences_raw.append(sentence)
        pos += match.end()

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
