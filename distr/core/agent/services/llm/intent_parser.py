"""IntentParser — extract a TOOL: call from a small-model free-text response.

Public API
----------
    parse(response_text, offered_tool_names) -> tuple[str, dict] | None

Complexity: O(n) in len(response_text). No blocking I/O.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Valid identifier pattern for tool names
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Loose match: catches TOOL:, TOOL CALL:, TOOLCALL:, TOOL_CALL:, TOOLCALLprint etc.
_LOOSE_TOOL_RE = re.compile(r"^tool[\s_]?call?\b", re.IGNORECASE)

# Normalise TOOL CALL: / TOOLCALL: / TOOL_CALL: → strip prefix
_NORMALISE_RE = re.compile(r"^tool[\s_]?call\s*:\s*", re.IGNORECASE)
# Plain TOOL: prefix
_PLAIN_TOOL_RE = re.compile(r"^tool\s*:\s*", re.IGNORECASE)
# Catches TOOLCALLsomething( — strip everything up to the first identifier+paren
_GARBAGE_PREFIX_RE = re.compile(r"^tool[\s_]?call\w*\s*\(?", re.IGNORECASE)

# Markdown fence pattern
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)


def _extract_lines(response_text: str) -> list[str]:
    """Return all lines to scan, including lines inside markdown fences.

    Splits only on \\n (not \\r) so that carriage returns inside quoted values
    remain part of the same line and are not treated as line separators.
    """
    # Normalise \r\n → \n but keep bare \r inside lines intact by only
    # splitting on \n after that normalisation.
    normalised = response_text.replace("\r\n", "\n")
    lines = normalised.split("\n")

    # Also extract lines from inside fenced code blocks (tolerance layer)
    fence_lines: list[str] = []
    fences = list(_FENCE_RE.finditer(normalised))
    if len(fences) >= 2:
        # Pair up fences and extract content between them
        i = 0
        while i + 1 < len(fences):
            start_fence = fences[i]
            end_fence = fences[i + 1]
            # Get content between the two fences
            newline_pos = normalised.find("\n", start_fence.start())
            if newline_pos == -1:
                i += 2
                continue
            content_start = newline_pos + 1
            content_end = end_fence.start()
            if content_start < content_end:
                inner = normalised[content_start:content_end]
                fence_lines.extend(inner.split("\n"))
            i += 2

    # Combine: original lines first, then fence-extracted lines
    # (deduplication not needed — first match wins)
    return lines + fence_lines


def _parse_args(arg_string: str) -> dict | None:
    """Parse a key=value argument string using a character-by-character state machine.

    States: EXPECT_KEY, IN_KEY, EXPECT_EQ, EXPECT_QUOTE, IN_VALUE, AFTER_VALUE, DONE

    Returns a dict on success, None on any parse error.
    An empty arg_string (zero args) returns {}.
    """
    arg_string = arg_string.strip()

    # Zero-arg case
    if not arg_string:
        return {}

    # States
    EXPECT_KEY = "EXPECT_KEY"
    IN_KEY = "IN_KEY"
    EXPECT_EQ = "EXPECT_EQ"
    EXPECT_QUOTE = "EXPECT_QUOTE"
    IN_VALUE = "IN_VALUE"
    AFTER_VALUE = "AFTER_VALUE"
    DONE = "DONE"

    state = EXPECT_KEY
    result: dict[str, str] = {}
    current_key = []
    current_value = []
    quote_char: str | None = None
    i = 0
    n = len(arg_string)

    while i < n and state != DONE:
        ch = arg_string[i]

        if state == EXPECT_KEY:
            if ch in (" ", "\t", "\n", "\r"):
                i += 1
                continue
            if ch == ")":
                # End of argument list (handles zero-arg after comma, or normal end)
                state = DONE
                i += 1
                continue
            if ch.isalpha() or ch == "_":
                state = IN_KEY
                current_key = [ch]
                i += 1
                continue
            # Anything else is invalid
            return None

        elif state == IN_KEY:
            if ch.isalnum() or ch == "_":
                current_key.append(ch)
                i += 1
                continue
            if ch in (" ", "\t"):
                state = EXPECT_EQ
                i += 1
                continue
            if ch == "=":
                state = EXPECT_QUOTE
                i += 1
                continue
            # Invalid character in key
            return None

        elif state == EXPECT_EQ:
            if ch in (" ", "\t"):
                i += 1
                continue
            if ch == "=":
                state = EXPECT_QUOTE
                i += 1
                continue
            return None

        elif state == EXPECT_QUOTE:
            if ch in (" ", "\t"):
                i += 1
                continue
            if ch in ('"', "'"):
                quote_char = ch
                state = IN_VALUE
                current_value = []
                i += 1
                continue
            # Unquoted value — not permitted
            return None

        elif state == IN_VALUE:
            if ch == "\\" and i + 1 < n:
                next_ch = arg_string[i + 1]
                if next_ch in ('"', "'", "\\"):
                    current_value.append(next_ch)
                    i += 2
                    continue
                else:
                    # Keep the backslash and the next char as-is
                    current_value.append(ch)
                    i += 1
                    continue
            if ch == quote_char:
                # End of quoted value
                key = "".join(current_key)
                value = "".join(current_value)
                result[key] = value
                current_key = []
                current_value = []
                quote_char = None
                state = AFTER_VALUE
                i += 1
                continue
            # Any other character (including commas and closing parens) is part of value
            current_value.append(ch)
            i += 1
            continue

        elif state == AFTER_VALUE:
            if ch in (" ", "\t", "\n", "\r"):
                i += 1
                continue
            if ch == ",":
                state = EXPECT_KEY
                i += 1
                continue
            if ch == ")":
                state = DONE
                i += 1
                continue
            return None

    # After consuming all characters
    if state in (EXPECT_KEY, AFTER_VALUE, DONE):
        return result

    # We ended mid-parse (e.g. unclosed quote, incomplete key)
    return None


def _try_positional_fallback(arg_string: str) -> dict | None:
    """Positional argument fallback (tolerance layer).

    If the arg string looks like a single quoted value (e.g. "some text" or 'some text'),
    return {"_arg0": unquoted_value} so the caller can decide what to do with it.
    """
    s = arg_string.strip()
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] == s[0]:
        # Strip outer quotes
        inner = s[1:-1]
        return {"_arg0": inner}
    return None


def parse(response_text: str, offered_tool_names: list[str]) -> tuple[str, dict] | None:
    """Scan response_text for a TOOL: line and parse it.

    Returns (tool_name, args_dict) if a valid TOOL: line is found,
    or None if no tool intent is detected or parsing fails.

    Does not perform I/O. O(n) in len(response_text).
    """
    lines = _extract_lines(response_text)

    for line in lines:
        stripped = line.strip()

        # Quick check: does this line look like a TOOL invocation at all?
        if not _LOOSE_TOOL_RE.match(stripped) and not _PLAIN_TOOL_RE.match(stripped):
            continue

        # Normalise TOOL CALL: / TOOLCALL: / TOOL_CALL: → strip the prefix entirely
        # then treat the rest as "tool_name(...)"
        m = _NORMALISE_RE.match(stripped)
        if m:
            rest = stripped[m.end():]
        else:
            m2 = _PLAIN_TOOL_RE.match(stripped)
            if m2:
                rest = stripped[m2.end():]
            else:
                # Garbage prefix: TOOLCALLprint(...), TOOLCALLtools.open(...)
                # Try to find the first real tool_name( pattern inside the line
                m3 = _GARBAGE_PREFIX_RE.match(stripped)
                if m3:
                    # Strip the garbage prefix and scan for tool_name( in what remains
                    rest = stripped[m3.end():]
                    # The rest might be "tools.openfile(path=...)" — try to extract
                    # the last dotted segment as the tool name
                    # Or it might be "openfile(path=...)" directly
                else:
                    continue

        # Find the opening paren
        paren_pos = rest.find("(")
        if paren_pos == -1:
            logger.warning("TOOL line looks like invocation but has no '(': %r", stripped)
            continue

        tool_name_candidate = rest[:paren_pos].strip()

        # Handle dotted names like "tools.openfile" → take the last segment
        if "." in tool_name_candidate:
            tool_name_candidate = tool_name_candidate.rsplit(".", 1)[-1].strip()

        # Validate tool name
        if not _TOOL_NAME_RE.match(tool_name_candidate):
            logger.warning(
                "TOOL line has invalid tool name %r: %r", tool_name_candidate, stripped
            )
            return None

        # Extract argument string: everything between first '(' and last ')'
        after_paren = rest[paren_pos + 1:]
        last_paren = after_paren.rfind(")")
        if last_paren == -1:
            logger.warning("TOOL line looks like invocation but has no closing ')': %r", stripped)
            continue

        arg_string = after_paren[:last_paren]

        # Try strict key=value parsing
        args = _parse_args(arg_string)
        if args is not None:
            return (tool_name_candidate, args)

        # Positional argument fallback (tolerance layer)
        positional = _try_positional_fallback(arg_string)
        if positional is not None:
            return (tool_name_candidate, positional)

        # All parsing failed — log warning with raw line
        logger.warning("TOOL line failed all parsing attempts: %r", stripped)
        return None

    return None
