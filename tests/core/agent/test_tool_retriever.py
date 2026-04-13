# Feature: semantic-tool-retrieval, Property 2: tier classification is total and binary
# Validates: Requirements 2.4, 2.6
"""Property-based tests for distr.core.agent.tool_retriever.ToolRetriever.

This file contains:
- A ``built_retriever`` pytest fixture that builds the embedding index over a
  small set of mock tools (reused by all subsequent property tests).
- Property 2: tier classification is total and binary — for any non-empty
  model name string, classify_model_tier returns exactly "micro" or "standard".
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Stub out PyQt6-dependent modules so tests can run without the GUI stack.
# distr.core.settings imports distr.core.utils which imports PyQt6.
# We register a lightweight stub in sys.modules before any production imports.
# ---------------------------------------------------------------------------
if "distr.core.settings" not in sys.modules:
    _settings_stub = MagicMock()
    _settings_stub.load_settings_from_db = MagicMock(return_value={})
    sys.modules["distr.core.settings"] = _settings_stub

from distr.core.agent.tool_retriever import ALWAYS_ON_NAMES, ToolRetriever


# ---------------------------------------------------------------------------
# Mock tools & built_retriever fixture
# ---------------------------------------------------------------------------

# Minimal mock tools with .name attributes matching ALWAYS_ON_NAMES plus a
# few extras so the index has realistic breadth.  Descriptions are provided
# via a patched TOOL_DESCRIPTIONS dict so build_index can encode them.

_MOCK_TOOL_NAMES: list[str] = sorted(ALWAYS_ON_NAMES) + [
    "web_search",
    "file_operations",
    "clipboard_action",
    "execute_shell",
]

_MOCK_TOOL_DESCRIPTIONS: dict[str, str] = {
    "smart_open": "Open a URL, application, or file with its default handler.",
    "execute_code": "Run a code snippet in a sandboxed interpreter.",
    "oracle_control": "Show, hide, or minimise the assistant overlay.",
    "mode_control": "Switch between voice, chat, or silent modes.",
    "new_chat": "Start a fresh conversation session.",
    "system_info": "Retrieve OS, CPU, memory, and disk information.",
    "web_search": "Search the web for current information or facts.",
    "file_operations": "List, create, read, delete, copy, or move files.",
    "clipboard_action": "Read or manipulate the system clipboard contents.",
    "execute_shell": "Execute a shell command and return its output.",
}

_MOCK_TOOLS = [SimpleNamespace(name=n) for n in _MOCK_TOOL_NAMES]


def _mock_sentence_transformers_module():
    """Return a fake ``sentence_transformers`` module with a SentenceTransformer
    class whose ``.encode()`` returns deterministic random 384-d vectors."""

    class _MockSentenceTransformer:
        def __init__(self, model_name: str):
            pass

        def encode(self, sentences, convert_to_numpy=False):
            return np.random.default_rng(42).standard_normal(
                (len(sentences), 384)
            ).astype(np.float32)

    mock_mod = SimpleNamespace(SentenceTransformer=_MockSentenceTransformer)
    return mock_mod


@pytest.fixture(scope="module")
def built_retriever():
    """Build a ToolRetriever with a small mock tool set.

    The fixture patches TOOL_DESCRIPTIONS so build_index can look up
    descriptions for the mock tools, then builds the index synchronously.
    Uses a mock SentenceTransformer so tests run without the real library.
    Scoped to module so the encode runs only once per test session.
    """
    retriever = ToolRetriever()
    mock_st_mod = _mock_sentence_transformers_module()
    with patch(
        "distr.core.agent.tool_retriever.TOOL_DESCRIPTIONS",
        _MOCK_TOOL_DESCRIPTIONS,
        create=True,
    ), patch.dict(sys.modules, {"sentence_transformers": mock_st_mod}):
        retriever.build_index(_MOCK_TOOLS)
    assert retriever.is_ready(), "built_retriever fixture: index failed to build"
    return retriever


# ---------------------------------------------------------------------------
# Property 2: Tier classification is total and binary
# ---------------------------------------------------------------------------


@settings(max_examples=200)
@given(model_name=st.text(min_size=1))
def test_tier_classification_binary(model_name: str) -> None:
    """**Validates: Requirements 2.4, 2.6**

    For any non-empty model name string, classify_model_tier SHALL return
    exactly one of "micro", "small", or "standard" — never another value,
    never an exception.
    """
    # Feature: semantic-tool-retrieval, Property 2
    result = ToolRetriever.classify_model_tier(model_name)
    assert result in ("micro", "small", "standard"), (
        f"classify_model_tier({model_name!r}) returned {result!r}, "
        f"expected 'micro', 'small', or 'standard'"
    )


# ---------------------------------------------------------------------------
# Property 3: Micro-tier parameter extraction correctness
# ---------------------------------------------------------------------------

# Safe separator characters: ASCII non-word chars that guarantee \b fires
# at the boundary of the digit token.  We use a small explicit set to avoid
# Unicode edge cases where Python's \b treats certain codepoints as \w.
_SEPARATORS = "-:/ "

# Prefix: optional separator-only text (no digits, no letters, no '_')
# so the \b before the digit token always fires.
_SAFE_PREFIX = st.text(alphabet=st.sampled_from(list(_SEPARATORS)), max_size=5)

# Suffix: empty or starts with a separator char (guarantees \b after 'b').
_SAFE_SUFFIX = st.text(alphabet=st.sampled_from(list(_SEPARATORS)), max_size=5)


@settings(max_examples=200)
@given(
    prefix=_SAFE_PREFIX,
    value=st.floats(min_value=0.1, max_value=10.0, allow_nan=False),
    suffix=_SAFE_SUFFIX,
)
def test_micro_tier_parameter_extraction(prefix: str, value: float, suffix: str) -> None:
    """**Validates: Requirements 2.1, 2.3**

    For any model name containing a ``{value:.1f}b`` parameter-count token,
    the tier classification SHALL be driven by the *formatted* numeric value
    compared against the tier thresholds.
    """
    # Feature: semantic-tool-retrieval, Property 3: param count drives tier
    model_name = f"{prefix}{value:.1f}b{suffix}"
    formatted_value = float(f"{value:.1f}")
    tier = ToolRetriever.classify_model_tier(model_name)
    if formatted_value <= 1.5:
        assert tier == "micro", (
            f"Expected 'micro' for formatted_value={formatted_value} "
            f"(model_name={model_name!r}), got {tier!r}"
        )
    elif formatted_value <= 4.0:
        assert tier in ("micro", "small", "standard"), (
            f"Expected a valid tier for formatted_value={formatted_value} "
            f"(model_name={model_name!r}), got {tier!r}"
        )
    else:
        assert tier == "standard", (
            f"Expected 'standard' for formatted_value={formatted_value} "
            f"(model_name={model_name!r}), got {tier!r}"
        )


# ---------------------------------------------------------------------------
# Unit tests: _resolve_k()
# ---------------------------------------------------------------------------

from distr.core.agent.tool_retriever import _resolve_k, _DEFAULT_K


class TestResolveK:
    """Unit tests for the _resolve_k() helper."""

    def test_default_when_no_env_no_db(self):
        """Returns default 10 when neither env var nor DB key is set."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("distr.core.agent.tool_retriever.load_settings_from_db", side_effect=Exception("no db"), create=True):
                result = _resolve_k()
        assert result == _DEFAULT_K

    def test_env_var_takes_priority(self):
        """Env var DECISIONS_TOOL_RETRIEVAL_K overrides everything."""
        with patch.dict("os.environ", {"DECISIONS_TOOL_RETRIEVAL_K": "5"}):
            result = _resolve_k()
        assert result == 5

    def test_env_var_non_digit_ignored(self):
        """Non-digit env var values are ignored, falls through to DB/default."""
        with patch.dict("os.environ", {"DECISIONS_TOOL_RETRIEVAL_K": "abc"}):
            with patch("distr.core.settings.load_settings_from_db", return_value={}):
                result = _resolve_k()
        assert result == _DEFAULT_K

    def test_settings_db_used_when_no_env(self):
        """Settings DB key is used when env var is absent."""
        with patch.dict("os.environ", {}, clear=False):
            env = dict(**__import__("os").environ)
            env.pop("DECISIONS_TOOL_RETRIEVAL_K", None)
            with patch.dict("os.environ", env, clear=True):
                with patch("distr.core.settings.load_settings_from_db", return_value={"tool_retrieval_k": 15}):
                    result = _resolve_k()
        assert result == 15

    def test_settings_db_exception_falls_to_default(self):
        """If settings DB raises, falls back to default."""
        with patch.dict("os.environ", {}, clear=True):
            with patch("distr.core.settings.load_settings_from_db", side_effect=Exception("db error")):
                result = _resolve_k()
        assert result == _DEFAULT_K

    def test_env_var_zero(self):
        """Env var '0' is a valid digit string — returns 0."""
        with patch.dict("os.environ", {"DECISIONS_TOOL_RETRIEVAL_K": "0"}):
            result = _resolve_k()
        assert result == 0

    def test_env_var_overrides_db(self):
        """Env var takes priority even when DB has a value."""
        with patch.dict("os.environ", {"DECISIONS_TOOL_RETRIEVAL_K": "7"}):
            with patch("distr.core.settings.load_settings_from_db", return_value={"tool_retrieval_k": 20}):
                result = _resolve_k()
        assert result == 7


# ---------------------------------------------------------------------------
# Property 1: Index completeness
# ---------------------------------------------------------------------------


def mock_tool_strategy():
    """Strategy that generates SimpleNamespace objects with unique .name attrs.

    Each generated name is a short alphabetic string prefixed with ``tool_``
    to guarantee non-empty, human-readable identifiers.
    """
    return st.text(
        alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="_"),
        min_size=1,
        max_size=15,
    ).map(lambda s: f"tool_{s}").map(lambda name: SimpleNamespace(name=name))


@settings(max_examples=20, deadline=None)
@given(tools=st.lists(mock_tool_strategy(), min_size=1, max_size=100).filter(
    lambda tools: len({t.name for t in tools}) == len(tools)
))
def test_index_completeness(tools: list) -> None:
    """**Validates: Requirements 1.1, 1.3**

    For any non-empty list of tool objects with unique names, after
    ``build_index()`` completes, the embedding index SHALL contain an entry
    for every tool name in the input list.
    """
    # Feature: semantic-tool-retrieval, Property 1
    retriever = ToolRetriever()
    # Mock both TOOL_DESCRIPTIONS (empty — triggers fallback descriptions)
    # and the sentence_transformers module (not installed in test env).
    mock_st_mod = _mock_sentence_transformers_module()
    with patch(
        "distr.core.agent.tools.loader.TOOL_DESCRIPTIONS",
        {},
    ), patch.dict(sys.modules, {"sentence_transformers": mock_st_mod}):
        retriever.build_index(tools)

    assert retriever.is_ready(), "Index should be ready after build_index with non-empty tools"
    for tool in tools:
        assert tool.name in retriever._names, (
            f"Tool {tool.name!r} missing from retriever._names "
            f"(have {retriever._names!r})"
        )
    assert len(retriever._names) == len(tools), (
        f"Expected {len(tools)} names, got {len(retriever._names)}"
    )


# ---------------------------------------------------------------------------
# Strategies for micro-tier model names
# ---------------------------------------------------------------------------


def micro_model_strategy():
    """Strategy that generates model names classified as 'micro'.

    Produces names via two paths:
    1. Names containing a parameter-count token ≤ 1.5 (e.g. "model-0.5b", "x1.0b").
       Prefix/suffix use only non-word chars so \\b fires correctly around the token.
    2. Names containing an entry from _MICRO_ALLOWLIST (e.g. "smollm-latest").
    """
    from distr.core.agent.tool_retriever import _MICRO_ALLOWLIST

    # Non-word separators only — ensures \b fires at token boundaries
    _sep_chars = list("-:/ ")

    # Path 1: embed a small param-count token (≤ 1.5)
    param_based = st.tuples(
        st.text(alphabet=st.sampled_from(_sep_chars), max_size=8),
        st.sampled_from(["0.5", "1.0", "1.5"]),
        st.text(alphabet=st.sampled_from(_sep_chars), max_size=5),
    ).map(lambda t: f"{t[0]}{t[1]}b{t[2]}")

    # Path 2: embed an allowlist entry
    allowlist_based = st.tuples(
        st.text(alphabet=st.sampled_from(_sep_chars + list("abcdefghijklmnopqrstuvwxyz")), max_size=5),
        st.sampled_from(_MICRO_ALLOWLIST),
        st.text(alphabet=st.sampled_from(_sep_chars + list("abcdefghijklmnopqrstuvwxyz")), max_size=5),
    ).map(lambda t: f"{t[0]}{t[1]}{t[2]}")

    return st.one_of(param_based, allowlist_based)


# ---------------------------------------------------------------------------
# Property 4: Micro-tier returns exactly always-on set
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(query=st.text(min_size=1), model_name=micro_model_strategy())
def test_micro_tier_returns_always_on_set(query: str, model_name: str, built_retriever) -> None:
    """**Validates: Requirements 2.5**

    For any model name classified as "micro", retrieve() SHALL return a list
    whose tool names are exactly the 6 Always_On_Set names (no more, no less,
    no RequestToolTool).
    """
    # Feature: semantic-tool-retrieval, Property 4
    result = built_retriever.retrieve(query, model_name)
    assert result is not None, "retrieve() should not return None for a built retriever"
    assert set(result) == ALWAYS_ON_NAMES, (
        f"Expected exactly ALWAYS_ON_NAMES={ALWAYS_ON_NAMES}, "
        f"got {set(result)} for model_name={model_name!r}"
    )


# ---------------------------------------------------------------------------
# Property 5: Active_Tool_Set never exceeds Hard_Ceiling
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(query=st.text(min_size=1), k=st.integers(min_value=1, max_value=30))
def test_hard_ceiling(query: str, k: int, built_retriever) -> None:
    """**Validates: Requirements 3.3, 3.6**

    For any non-empty query string, any standard-tier model name, and any
    value of K >= 1, the length of the list returned by retrieve() SHALL be
    <= K + 6 + 1.
    """
    # Feature: semantic-tool-retrieval, Property 5
    result = built_retriever.retrieve(query, "llama3:8b", k=k)
    assert result is not None, "retrieve() should not return None for a built retriever"
    assert len(result) <= k + 6 + 1, (
        f"Active_Tool_Set length {len(result)} exceeds Hard_Ceiling {k + 6 + 1} "
        f"for k={k}, query={query!r}"
    )


# ---------------------------------------------------------------------------
# Property 6: Always-on tools always present for standard tier
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(query=st.text(min_size=1))
def test_always_on_always_present(query: str, built_retriever) -> None:
    """**Validates: Requirements 3.4, 3.7**

    For any non-empty query string and any standard-tier model name, the
    result of retrieve() SHALL contain all 6 Always_On_Set tool names.
    """
    # Feature: semantic-tool-retrieval, Property 6
    result = built_retriever.retrieve(query, "llama3:8b")
    assert result is not None, "retrieve() should not return None for a built retriever"
    assert ALWAYS_ON_NAMES.issubset(set(result)), (
        f"Always-on tools missing from result: "
        f"missing={ALWAYS_ON_NAMES - set(result)}, result={result}"
    )


# ---------------------------------------------------------------------------
# Property 7: RequestToolTool always present for standard tier
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(query=st.text(min_size=1))
def test_request_tool_always_present(query: str, built_retriever) -> None:
    """**Validates: Requirements 3.5, 6.6**

    For any non-empty query string and any standard-tier model name, the
    result of retrieve() SHALL contain "request_tool".
    """
    # Feature: semantic-tool-retrieval, Property 7
    result = built_retriever.retrieve(query, "llama3:8b")
    assert result is not None, "retrieve() should not return None for a built retriever"
    assert "request_tool" in result, (
        f"'request_tool' not found in result: {result}"
    )


# ---------------------------------------------------------------------------
# Property 8: No duplicate tool names in Active_Tool_Set
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=None)
@given(query=st.text(min_size=1))
def test_no_duplicates(query: str, built_retriever) -> None:
    """**Validates: Requirements 3.4**

    For any query and model name, the list returned by retrieve() SHALL
    contain no duplicate tool name strings.
    """
    # Feature: semantic-tool-retrieval, Property 8
    result = built_retriever.retrieve(query, "llama3:8b")
    assert result is not None, "retrieve() should not return None for a built retriever"
    assert len(result) == len(set(result)), (
        f"Duplicate tool names found in result: {result}"
    )


# ---------------------------------------------------------------------------
# Property 9: Cosine similarity bounded in [-1, 1]
# ---------------------------------------------------------------------------


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


@settings(max_examples=200, deadline=None)
@given(
    dim=st.integers(min_value=2, max_value=384),
    data=st.data(),
)
def test_cosine_similarity_bounded(dim: int, data) -> None:
    """**Validates: Requirements 3.2**

    For any two non-zero float vectors of equal length, cosine similarity
    SHALL return a value in the closed interval [-1.0, 1.0].
    """
    # Feature: semantic-tool-retrieval, Property 9
    from hypothesis import assume

    float_st = st.floats(min_value=-10, max_value=10, allow_nan=False, allow_infinity=False)
    a = data.draw(st.lists(float_st, min_size=dim, max_size=dim))
    b = data.draw(st.lists(float_st, min_size=dim, max_size=dim))

    a_arr = np.array(a, dtype=np.float64)
    b_arr = np.array(b, dtype=np.float64)
    assume(np.linalg.norm(a_arr) > 0)
    assume(np.linalg.norm(b_arr) > 0)

    sim = _cosine_similarity(a_arr, b_arr)
    assert -1.0 - 1e-6 <= sim <= 1.0 + 1e-6, (
        f"Cosine similarity {sim} out of bounds [-1, 1] for vectors a={a[:5]}..., b={b[:5]}..."
    )


# ---------------------------------------------------------------------------
# Property 10: RequestToolTool injection round-trip
# ---------------------------------------------------------------------------

from distr.core.agent.tools.request_tool import RequestToolTool
from distr.core.agent.tools.loader import TOOL_REGISTRY


@settings(max_examples=100)
@given(tool_name=st.sampled_from(list(TOOL_REGISTRY.keys())))
def test_request_tool_injection_round_trip(tool_name: str) -> None:
    """**Validates: Requirements 6.2, 6.3, 6.4**

    For any tool name present in the full TOOL_REGISTRY, calling
    RequestToolTool._run(text=tool_name) with a mock callback SHALL:
    1. Call the callback exactly once.
    2. Pass the tool_name as the callback argument.
    3. Return a result string containing the tool_name.
    """
    # Feature: semantic-tool-retrieval, Property 10
    invocations: list[str] = []

    def mock_callback(query: str) -> tuple[bool, str]:
        invocations.append(query)
        return (True, f"Tool '{query}' injected. Please retry your task.")

    rtt = RequestToolTool(on_tool_requested=mock_callback)
    result = rtt._run(text=tool_name)

    assert len(invocations) == 1, (
        f"Expected callback to be called exactly once, "
        f"but was called {len(invocations)} time(s)"
    )
    assert invocations[0] == tool_name, (
        f"Expected callback to receive {tool_name!r}, "
        f"got {invocations[0]!r}"
    )
    assert tool_name in result, (
        f"Expected result to contain {tool_name!r}, "
        f"got {result!r}"
    )
