# Feature: llm-settings-layout-fix, Property 1: Bug Condition - Provider Select Uses Flex Layout
# Validates: Requirements 1.1, 1.2
"""Property-based test: for any LLM row (conversational, coding, vision, image),
the provider select wrapper div must use class 'flex-1' and must NOT use class 'w-36'.

This test is expected to FAIL on unfixed code, confirming the bug exists.
"""

import os
from html.parser import HTMLParser

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# HTML parsing helper
# ---------------------------------------------------------------------------

TEMPLATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "distr",
    "gui",
    "web",
    "templates",
    "settings",
    "sections",
    "llms.html",
)

LLM_ROWS = ["conversational", "coding", "vision", "image"]


class ProviderWrapperExtractor(HTMLParser):
    """Extract the class attribute of the <div> that is the immediate parent
    wrapper of each ``<select id="{row}_provider">``."""

    def __init__(self):
        super().__init__()
        # Maps row name -> class string of the wrapper div
        self.wrapper_classes: dict[str, str] = {}
        # Track open tags as a stack of (tag, attrs-dict)
        self._stack: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self._stack.append((tag, attrs_dict))

        if tag == "select":
            select_id = attrs_dict.get("id", "")
            for row in LLM_ROWS:
                if select_id == f"{row}_provider":
                    # Walk back up the stack to find the parent div
                    for i in range(len(self._stack) - 2, -1, -1):
                        ptag, pattrs = self._stack[i]
                        if ptag == "div":
                            self.wrapper_classes[row] = pattrs.get("class", "")
                            break

    def handle_endtag(self, tag):
        # Pop from stack (simplified; good enough for well-formed HTML)
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                self._stack.pop(i)
                break


def _get_provider_wrapper_classes() -> dict[str, str]:
    """Parse the template and return a mapping of row -> wrapper div classes."""
    with open(TEMPLATE_PATH, "r") as f:
        html = f.read()
    parser = ProviderWrapperExtractor()
    parser.feed(html)
    return parser.wrapper_classes


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------

llm_row_strategy = st.sampled_from(LLM_ROWS)

# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


@settings(max_examples=50)
@given(row=llm_row_strategy)
def test_provider_wrapper_uses_flex1_not_w36(row: str) -> None:
    """**Validates: Requirements 1.1, 1.2**

    For any LLM row chosen from {conversational, coding, vision, image},
    the provider select wrapper div MUST have class 'flex-1' and MUST NOT
    have class 'w-36'.

    On unfixed code this test is expected to FAIL, confirming the bug exists.
    """
    wrapper_classes = _get_provider_wrapper_classes()

    assert row in wrapper_classes, (
        f"Could not find provider wrapper div for row '{row}'"
    )

    classes = wrapper_classes[row].split()

    assert "flex-1" in classes, (
        f"Bug detected: '{row}' provider wrapper has classes '{wrapper_classes[row]}' "
        f"— expected 'flex-1' but it is missing"
    )
    assert "w-36" not in classes, (
        f"Bug detected: '{row}' provider wrapper still has 'w-36' class "
        f"(classes: '{wrapper_classes[row]}')"
    )
