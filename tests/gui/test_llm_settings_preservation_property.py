# Feature: llm-settings-layout-fix, Property 2: Preservation - Non-Provider Elements Unchanged
# Validates: Requirements 3.1, 3.2, 3.3
"""Property-based tests: for any element in the LLM settings template that is NOT
a provider select wrapper div in an LLM row, the element's classes and attributes
must remain identical between the original and fixed templates.

These tests are written BEFORE the fix and MUST PASS on unfixed code,
establishing the baseline that must be preserved after the fix.
"""

import os
import re
from html.parser import HTMLParser

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Template path
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


def _read_template() -> str:
    """Read the raw HTML template."""
    with open(TEMPLATE_PATH, "r") as f:
        return f.read()


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------


def _extract_section(html: str, heading_text: str) -> str | None:
    """Extract a full section block by finding the heading and its parent div."""
    pattern = rf'<h3[^>]*>{re.escape(heading_text)}</h3>'
    match = re.search(pattern, html)
    if not match:
        return None
    # Walk backwards to find the opening <div of the section
    before = html[:match.start()]
    # Find the last <div that starts a section block before the heading
    div_pattern = r'<div\s+class="border border-\[#565869\] rounded-lg p-5 bg-\[#1a1f3a\]">'
    div_matches = list(re.finditer(div_pattern, before))
    if not div_matches:
        return None
    section_start = div_matches[-1].start()
    # Now find the matching closing </div>
    depth = 0
    i = section_start
    while i < len(html):
        if html[i:i + 4] == '<div':
            depth += 1
        elif html[i:i + 6] == '</div>':
            depth -= 1
            if depth == 0:
                return html[section_start:i + 6]
        i += 1
    return None


def _extract_stt_section(html: str) -> str:
    """Extract the full STT section HTML."""
    result = _extract_section(html, "Speech to Text (STT)")
    assert result is not None, "Could not find STT section in template"
    return result


class ElementExtractor(HTMLParser):
    """Extract elements matching specific criteria from the template."""

    def __init__(self):
        super().__init__()
        self.labels: list[dict[str, str | None]] = []
        self.buttons: list[dict[str, str | None]] = []
        self.model_selects: list[dict[str, str | None]] = []
        self.flex_rows: list[dict[str, str | None]] = []
        self.section_headings: list[str] = []
        self._in_heading = False
        self._heading_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "") or ""

        # Collect labels with w-20 or w-14
        if tag == "label" and ("w-20" in cls or "w-14" in cls or "w-16" in cls):
            self.labels.append(attrs_dict)

        # Collect download buttons with w-8
        if tag == "button" and "w-8" in cls:
            self.buttons.append(attrs_dict)

        # Collect model selects (flex-1 selects)
        if tag == "select" and "flex-1" in cls:
            self.model_selects.append(attrs_dict)

        # Collect flex rows
        if tag == "div" and "flex" in cls and "items-center" in cls and "gap-4" in cls:
            self.flex_rows.append(attrs_dict)

        # Track section headings
        if tag == "h3":
            self._in_heading = True
            self._heading_text = ""

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3" and self._in_heading:
            self._in_heading = False
            self.section_headings.append(self._heading_text.strip())


def _extract_elements(html: str) -> ElementExtractor:
    """Parse the template and extract all relevant elements."""
    extractor = ElementExtractor()
    extractor.feed(html)
    return extractor


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

llm_row_strategy = st.sampled_from(LLM_ROWS)

label_type_strategy = st.sampled_from(["provider", "model"])

section_heading_strategy = st.sampled_from([
    "Speech to Text (STT)",
    "Conversational LLM",
    "Coding LLM",
    "Vision LLM",
    "Image LLM",
])


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


@settings(max_examples=50)
@given(data=st.data())
def test_stt_section_html_unchanged(data: st.DataObject) -> None:
    """**Validates: Requirements 3.1**

    The STT section must have a single model select with flex-1 and no
    provider wrapper div. This test records the STT section structure
    and verifies it matches the expected baseline.
    """
    html = _read_template()
    stt_html = _extract_stt_section(html)

    # STT section must exist
    assert stt_html is not None, "STT section not found in template"

    # STT section must contain a model select with flex-1
    assert 'id="stt_model"' in stt_html, "STT section missing stt_model select"
    assert "flex-1" in stt_html, "STT model select missing flex-1 class"

    # STT section must NOT contain any provider select or w-36 wrapper
    assert "provider" not in stt_html.lower() or "stt_provider" not in stt_html, (
        "STT section should not have a provider select"
    )
    assert "w-36" not in stt_html, (
        "STT section should not contain w-36 class"
    )

    # STT section must have the w-16 label for Model
    assert "w-16" in stt_html, "STT section missing w-16 label width"

    # Draw a random boolean to exercise the property across multiple runs
    _ = data.draw(st.booleans())


@settings(max_examples=50)
@given(row=llm_row_strategy)
def test_label_widths_preserved(row: str) -> None:
    """**Validates: Requirements 3.2**

    For any LLM row, the Provider label must have w-20 and the Model
    label must have w-14. These fixed widths must be preserved.
    """
    html = _read_template()
    elements = _extract_elements(html)

    # Find labels for this specific row
    provider_label = None
    model_label = None
    for label in elements.labels:
        label_for = label.get("for", "")
        if label_for == f"{row}_provider":
            provider_label = label
        elif label_for == f"{row}_model":
            model_label = label

    assert provider_label is not None, (
        f"Provider label not found for row '{row}'"
    )
    assert model_label is not None, (
        f"Model label not found for row '{row}'"
    )

    provider_classes = (provider_label.get("class", "") or "").split()
    model_classes = (model_label.get("class", "") or "").split()

    assert "w-20" in provider_classes, (
        f"Provider label for '{row}' missing w-20 class, has: {provider_classes}"
    )
    assert "w-14" in model_classes, (
        f"Model label for '{row}' missing w-14 class, has: {model_classes}"
    )


@settings(max_examples=50)
@given(row=llm_row_strategy)
def test_download_button_sizes_preserved(row: str) -> None:
    """**Validates: Requirements 3.2**

    For any LLM row, the download button must have w-8 and h-8 classes.
    """
    html = _read_template()
    elements = _extract_elements(html)

    # Find the download button for this row
    download_btn = None
    for btn in elements.buttons:
        btn_id = btn.get("id", "")
        if btn_id == f"{row}_download":
            download_btn = btn
            break

    assert download_btn is not None, (
        f"Download button not found for row '{row}'"
    )

    btn_classes = (download_btn.get("class", "") or "").split()
    assert "w-8" in btn_classes, (
        f"Download button for '{row}' missing w-8 class, has: {btn_classes}"
    )
    assert "h-8" in btn_classes, (
        f"Download button for '{row}' missing h-8 class, has: {btn_classes}"
    )


@settings(max_examples=50)
@given(row=llm_row_strategy)
def test_model_select_flex1_preserved(row: str) -> None:
    """**Validates: Requirements 3.2, 3.3**

    For any LLM row, the model select must use flex-1 class.
    """
    html = _read_template()
    elements = _extract_elements(html)

    # Find the model select for this row
    model_select = None
    for sel in elements.model_selects:
        sel_id = sel.get("id", "")
        if sel_id == f"{row}_model":
            model_select = sel
            break

    assert model_select is not None, (
        f"Model select not found for row '{row}'"
    )

    select_classes = (model_select.get("class", "") or "").split()
    assert "flex-1" in select_classes, (
        f"Model select for '{row}' missing flex-1 class, has: {select_classes}"
    )


@settings(max_examples=50)
@given(row=st.sampled_from(LLM_ROWS + ["stt"]))
def test_flex_row_layout_preserved(row: str) -> None:
    """**Validates: Requirements 3.1, 3.2**

    For any section row (including STT), the flex row container must
    use 'flex items-center gap-4' layout classes.
    """
    html = _read_template()

    # Map row names to the select IDs we expect inside each flex row
    if row == "stt":
        select_id = "stt_model"
    else:
        select_id = f"{row}_provider"

    # Find the flex row containing this select
    # Parse to find the div with flex items-center gap-4 that contains the select
    pattern = r'<div\s+class="flex items-center gap-4">\s*.*?' + re.escape(f'id="{select_id}"')
    match = re.search(pattern, html, re.DOTALL)

    assert match is not None, (
        f"Could not find flex row (flex items-center gap-4) containing "
        f"select '{select_id}' for row '{row}'"
    )


@settings(max_examples=50)
@given(heading=section_heading_strategy)
def test_section_headings_preserved(heading: str) -> None:
    """**Validates: Requirements 3.1, 3.2**

    All section headings must be present in the template.
    """
    html = _read_template()
    elements = _extract_elements(html)

    assert heading in elements.section_headings, (
        f"Section heading '{heading}' not found in template. "
        f"Found: {elements.section_headings}"
    )
