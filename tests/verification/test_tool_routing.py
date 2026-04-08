"""
Tool Routing Verification Tests

Two test layers:
  1. Router tests — does the semantic router include the right tool? (no LLM, fast)
  2. LLM tests — does the LLM pick the right tool from the candidate set? (needs Ollama)

Run:
  python -m pytest tests/verification/test_tool_routing.py -v
  python -m pytest tests/verification/test_tool_routing.py -v -k router   # router only (fast)
  python -m pytest tests/verification/test_tool_routing.py -v -k llm      # LLM only (slower)

Or standalone:
  python tests/verification/test_tool_routing.py              # both layers
  python tests/verification/test_tool_routing.py --router     # router only
  python tests/verification/test_tool_routing.py --llm        # LLM only
"""

import json
import os
import sys
import time
import logging

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Test cases: (user_text, expected_tool_name)
#
# Each entry says: "when the user says X, the system should
# route to tool Y". Add new cases as you discover misroutes.
# ──────────────────────────────────────────────────────────────

ROUTING_CASES = [
    # ── File operations ──
    ("delete the WhatsApp image in my downloads folder", "file_operations"),
    ("what files are on my desktop", "file_operations"),
    ("list the files in my documents folder", "file_operations"),
    ("create a new file called notes.txt", "file_operations"),
    ("rename that file to report.md", "file_operations"),
    ("move that file to my desktop", "file_operations"),

    # ── Document conversion ──
    ("convert that into a PDF document", "convert_document"),
    ("convert the file I just dropped to PDF", "convert_document"),
    ("turn this into a word document", "convert_document"),
    ("export as PDF", "convert_document"),
    ("make a PDF of this", "convert_document"),

    # ── Audio/image conversion ──
    ("convert this to mp3", "file_converter"),
    ("convert those flac files to wav", "file_converter"),
    ("convert the image to webp", "file_converter"),

    # ── Screenshot & vision ──
    ("take a screenshot", "screenshot_analyzer"),
    ("what do you see on screen", "screenshot_analyzer"),
    ("capture the screen", "screenshot_analyzer"),
    ("describe what's on my screen", "screenshot_analyzer"),

    # ── Clipboard ──
    ("what is in my clipboard", "clipboard_action"),
    ("read my clipboard", "clipboard_action"),
    ("explain this", "clipboard_action"),
    ("elaborate on this", "clipboard_action"),

    # ── Text editing ──
    ("copy this", "text_editing"),
    ("paste", "text_editing"),
    ("select all", "text_editing"),  # NOTE: handled by fast action regex, not router
    ("undo", "text_editing"),

    # ── Git ──
    ("commit and push", "git_operations"),
    ("git status", "git_operations"),
    ("show me the git diff", "git_operations"),
    ("pull the latest changes", "git_operations"),

    # ── Web ──
    ("search the web for python tutorials", "web_search"),
    ("look up the weather", "web_search"),
    ("google how to make pasta", "web_search"),

    # ── Media ──
    ("play the next track", "media_control"),
    ("pause the music", "media_control"),
    ("turn the volume up", "media_control"),

    # ── Open apps ──
    ("open Chrome", "smart_open"),
    ("launch Finder", "smart_open"),
    ("open Safari", "smart_open"),

    # ── Telegram ──
    ("send this to telegram", "send_file_to_telegram"),
    ("send that file to telegram", "send_file_to_telegram"),

    # ── Transcription ──
    ("transcribe this audio file", "audio_transcriber"),
    ("transcribe the recording", "audio_transcriber"),

    # ── Image generation ──
    ("create an image of a sunset", "image_generator"),
    ("generate a logo for my company", "image_generator"),

    # ── Step runner ──
    ("create a step runner", "create_step_runner"),
    ("create an automation", "create_step_runner"),

    # ── Projects ──
    ("list my projects", "list_projects"),
    ("which project is active", "query_current_project"),
    ("switch to the other project", "switch_project"),

    # ── Snippets ──
    ("create a snippet", "create_snippet"),
    ("use my snippet", "use_snippet"),

    # ── Execute code ──
    ("run this python script", "execute_code"),
    ("execute the code", "execute_code"),

    # ── System ──
    ("exit the app", "exit_app"),
    ("quit", "exit_app"),

    # ── Chat ──
    ("start a new chat", "new_chat"),
    ("clear the chat history", "clear_chat"),

    # ── Google ──
    ("upload this to google drive", "upload_doc_to_google"),

    # ── Kanban ──
    ("create a ticket for this bug", "create_ticket"),
]


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────

_tools_cache = None
_router_cache = None


def _get_tools():
    """Load tools once, cache for all tests."""
    global _tools_cache
    if _tools_cache is None:
        from distr.core.agent.tools.loader import load_tools
        _tools_cache = load_tools(use_navigation_tools=True)
    return _tools_cache


def _get_router():
    """Build retriever once, cache for all tests."""
    global _router_cache
    if _router_cache is None:
        from distr.core.agent.tool_retriever import ToolRetriever
        _router_cache = ToolRetriever()
        _router_cache.build_index(_get_tools())
    return _router_cache



# ──────────────────────────────────────────────────────────────
# Layer 1: Router tests (fast, no LLM)
# Verifies the semantic router includes the expected tool
# in the candidate set it would pass to the LLM.
# ──────────────────────────────────────────────────────────────

# Commands that are handled by the fast action regex detector, not the router.
# The router doesn't need to catch these — they never reach it.
FAST_ACTION_ONLY = frozenset(["select all", "paste", "undo"])


@pytest.mark.parametrize("user_text,expected_tool", ROUTING_CASES, ids=[c[0][:50] for c in ROUTING_CASES])
def test_router_includes_expected_tool(user_text, expected_tool):
    """The semantic retriever should include the expected tool in its candidate set."""
    if user_text in FAST_ACTION_ONLY:
        pytest.skip(f"'{user_text}' is handled by fast action regex, not router")

    retriever = _get_router()
    if not retriever.is_ready():
        pytest.skip("ToolRetriever not ready (embedding model unavailable)")

    selected_names = retriever.retrieve(user_text, "llama3:8b")
    if selected_names is None:
        pytest.skip("ToolRetriever returned None (kill switch or index not ready)")

    assert expected_tool in selected_names, (
        f"Retriever did not include '{expected_tool}' for: \"{user_text}\"\n"
        f"  Selected: {selected_names}"
    )


# ──────────────────────────────────────────────────────────────
# Layer 2: LLM tool selection tests (needs Ollama running)
# Sends the user text + narrowed tool schemas to the LLM,
# checks which tool it picks — without executing anything.
# ──────────────────────────────────────────────────────────────

def _check_ollama_available():
    """Check if Ollama is running and has a chat model."""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            # Find a small chat model
            for m in models:
                name = m.get("name", "")
                if any(x in name.lower() for x in ["qwen", "llama", "gemma", "mistral"]):
                    return name
        return None
    except Exception:
        return None


def _tool_to_ollama_schema(tool) -> dict:
    """Convert a tool instance to Ollama function-calling schema."""
    # Get the schema from the tool's args_schema if available
    parameters = {"type": "object", "properties": {}, "required": []}
    if hasattr(tool, "args_schema") and tool.args_schema:
        try:
            schema = tool.args_schema.model_json_schema()
            parameters = {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            }
        except Exception:
            pass

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (getattr(tool, "description", "") or "")[:500],
            "parameters": parameters,
        },
    }


def _ask_llm_for_tool(user_text: str, tools: list, model: str) -> dict:
    """Send user text + tool schemas to LLM, return the tool call (no execution).

    Returns {"tool": "tool_name", "args": {...}} or {"tool": None} if no tool picked.
    """
    import requests

    tool_schemas = [_tool_to_ollama_schema(t) for t in tools]

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a voice assistant. When the user gives a command, "
                    "call the appropriate tool. Do not explain, just call the tool."
                ),
            },
            {"role": "user", "content": user_text},
        ],
        "tools": tool_schemas,
        "stream": False,
    }

    resp = requests.post(
        "http://localhost:11434/api/chat",
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    data = resp.json()

    message = data.get("message", {})
    tool_calls = message.get("tool_calls", [])

    if tool_calls:
        tc = tool_calls[0]
        func = tc.get("function", {})
        return {
            "tool": func.get("name"),
            "args": func.get("arguments", {}),
        }

    return {"tool": None, "args": {}}


# Subset of cases for LLM testing (these are the most important ones
# and the ones that have historically been misrouted)
LLM_CRITICAL_CASES = [
    ("delete the WhatsApp image in my downloads folder", ["file_operations"]),
    ("convert that into a PDF document", ["convert_document"]),
    ("take a screenshot", ["screenshot_analyzer"]),
    ("what is in my clipboard", ["clipboard_action"]),
    ("copy this", ["text_editing"]),
    ("commit and push", ["git_operations"]),
    ("search the web for python tutorials", ["web_search"]),
    ("play the next track", ["media_control"]),
    ("open Chrome", ["smart_open", "open_window"]),  # both valid
    ("send this to telegram", ["send_file_to_telegram", "send_voice_note_to_telegram"]),  # ambiguous without context
    ("transcribe this audio file", ["audio_transcriber"]),
    ("create an image of a sunset", ["image_generator"]),
    ("list the files in my documents folder", ["file_operations"]),
    ("convert this to mp3", ["file_converter"]),
    ("exit the app", ["exit_app"]),
    ("create a step runner for me", ["create_step_runner"]),
    ("what files are on my desktop", ["file_operations"]),
    ("explain this", ["clipboard_action"]),
    ("run this python script for me", ["execute_code"]),
    ("upload this to google drive", ["upload_doc_to_google"]),
]


@pytest.mark.parametrize(
    "user_text,expected_tools",
    LLM_CRITICAL_CASES,
    ids=[c[0][:50] for c in LLM_CRITICAL_CASES],
)
def test_llm_picks_correct_tool(user_text, expected_tools):
    """The LLM should pick one of the expected tools from the router's candidate set."""
    model = _check_ollama_available()
    if not model:
        pytest.skip("Ollama not running or no chat model available")

    router = _get_router()
    tools = _get_tools()

    # Get the narrowed tool set from the router
    if router.is_ready:
        candidate_tools = router.route(user_text, tools)
    else:
        candidate_tools = tools

    # Ask the LLM which tool to use (no execution)
    result = _ask_llm_for_tool(user_text, candidate_tools, model)
    picked = result["tool"]

    assert picked in expected_tools, (
        f"LLM picked '{picked}' instead of one of {expected_tools} for: \"{user_text}\"\n"
        f"  Args: {result['args']}\n"
        f"  Candidates: {[t.name for t in candidate_tools]}\n"
        f"  Model: {model}"
    )


# ──────────────────────────────────────────────────────────────
# Standalone runner with nice output
# ──────────────────────────────────────────────────────────────

def _run_router_tests():
    """Run router tests with formatted output."""
    print("\n" + "=" * 70)
    print("LAYER 1: Semantic Router (does the right tool make the candidate set?)")
    print("=" * 70)

    router = _get_router()
    if not router.is_ready:
        print("SKIP: ToolRouter not ready (Ollama embedding unavailable)")
        return 0, 0

    tools = _get_tools()
    passed = 0
    failed = 0

    for user_text, expected_tool in ROUTING_CASES:
        selected = router.route(user_text, tools)
        selected_names = [t.name for t in selected]
        scores = router.get_scores(user_text)
        rank = next((i + 1 for i, (n, _) in enumerate(scores) if n == expected_tool), "?")
        sim = next((s for n, s in scores if n == expected_tool), 0)

        if expected_tool in selected_names:
            print(f"  ✅ \"{user_text[:55]}\" -> {expected_tool} (rank={rank}, sim={sim:.3f})")
            passed += 1
        else:
            top3 = [(n, f"{s:.3f}") for n, s in scores[:3]]
            print(f"  ❌ \"{user_text[:55]}\" -> MISSING {expected_tool} (rank={rank}, sim={sim:.3f})")
            print(f"     Top 3: {top3}")
            failed += 1

    print(f"\nRouter: {passed}/{passed + failed} passed")
    return passed, failed


def _run_llm_tests():
    """Run LLM tests with formatted output."""
    print("\n" + "=" * 70)
    print("LAYER 2: LLM Tool Selection (does the LLM pick the right tool?)")
    print("=" * 70)

    model = _check_ollama_available()
    if not model:
        print("SKIP: Ollama not running or no chat model available")
        return 0, 0

    print(f"Using model: {model}\n")

    router = _get_router()
    tools = _get_tools()
    passed = 0
    failed = 0

    for user_text, expected_tools in LLM_CRITICAL_CASES:
        if router.is_ready:
            candidate_tools = router.route(user_text, tools)
        else:
            candidate_tools = tools

        t0 = time.time()
        try:
            result = _ask_llm_for_tool(user_text, candidate_tools, model)
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  ⏱️  \"{user_text[:55]}\" -> TIMEOUT ({elapsed:.0f}s)")
            failed += 1
            continue
        elapsed = time.time() - t0
        picked = result["tool"]

        if picked in expected_tools:
            print(f"  ✅ \"{user_text[:55]}\" -> {picked} ({elapsed:.1f}s)")
            passed += 1
        else:
            print(f"  ❌ \"{user_text[:55]}\" -> {picked} (expected {expected_tools}, {elapsed:.1f}s)")
            print(f"     Candidates: {[t.name for t in candidate_tools[:10]]}")
            failed += 1

    print(f"\nLLM: {passed}/{passed + failed} passed")
    return passed, failed


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tool routing verification")
    parser.add_argument("--router", action="store_true", help="Run router tests only")
    parser.add_argument("--llm", action="store_true", help="Run LLM tests only")
    args = parser.parse_args()

    run_both = not args.router and not args.llm
    total_passed = 0
    total_failed = 0

    if args.router or run_both:
        p, f = _run_router_tests()
        total_passed += p
        total_failed += f

    if args.llm or run_both:
        p, f = _run_llm_tests()
        total_passed += p
        total_failed += f

    print(f"\n{'=' * 70}")
    print(f"TOTAL: {total_passed}/{total_passed + total_failed} passed")
    if total_failed:
        print(f"       {total_failed} FAILED")
        sys.exit(1)
    else:
        print("       All tests passed")
