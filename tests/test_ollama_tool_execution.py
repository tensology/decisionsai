"""
End-to-end test: Ollama model receives filtered tools, picks one, executes it.
Measures total latency from prompt → tool selection → tool execution → response.
"""
import sys, os, time, json, requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

OLLAMA_URL = "http://localhost:11434"

def get_models():
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return []

def tool_to_ollama_schema(tool):
    params = {"type": "object", "properties": {}, "required": []}
    if hasattr(tool, "args_schema") and tool.args_schema:
        try:
            s = tool.args_schema.model_json_schema()
            params = {"type": "object", "properties": s.get("properties", {}), "required": s.get("required", [])}
        except Exception:
            pass
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (getattr(tool, "description", "") or "")[:500],
            "parameters": params,
        },
    }

# ── Setup ──
print("=" * 70)
print("OLLAMA TOOL EXECUTION TEST")
print("=" * 70)

from distr.core.agent.tools.loader import load_tools
from distr.core.agent.tool_retriever import ToolRetriever, ALWAYS_ON_NAMES

tools = load_tools(use_navigation_tools=True)
tool_by_name = {t.name: t for t in tools}
print(f"Loaded {len(tools)} tools")

retriever = ToolRetriever()
retriever.build_index(tools)
print(f"Index ready: {retriever.is_ready()} (backend: {retriever._backend})")

models = get_models()
if not models:
    print("Ollama not running — cannot proceed")
    sys.exit(1)

# Pick models to test: one small, one medium
test_models = []
for m in models:
    tier = ToolRetriever.classify_model_tier(m)
    if tier == "standard" and "qwen" in m.lower() and "8b" in m.lower():
        test_models.append(m)
    if tier == "standard" and "llama" in m.lower() and "3b" in m.lower():
        test_models.append(m)
    if tier == "standard" and "gemma" in m.lower() and "4b" in m.lower():
        test_models.append(m)
if not test_models:
    # Fallback: use first standard-tier model
    for m in models:
        if ToolRetriever.classify_model_tier(m) == "standard":
            test_models.append(m)
            break
if not test_models:
    print("No standard-tier models found in Ollama")
    sys.exit(1)

print(f"Testing with: {test_models}")

# ── Test cases ──
TEST_CASES = [
    {
        "prompt": "Create a file called retrieval_test.txt on my desktop with the content 'Hello from semantic retrieval test'",
        "expected_tool": "file_operations",
    },
    {
        "prompt": "What is my operating system and how much RAM do I have?",
        "expected_tool": "system_info",
    },
    {
        "prompt": "List the files on my desktop",
        "expected_tool": "file_operations",
    },
]

for model in test_models:
    print(f"\n{'─' * 70}")
    print(f"MODEL: {model}")
    print(f"{'─' * 70}")

    for tc in TEST_CASES:
        prompt = tc["prompt"]
        expected = tc["expected_tool"]

        # 1. Retrieval
        t_ret = time.time()
        names = retriever.retrieve(prompt, model, k=10)
        retrieval_ms = (time.time() - t_ret) * 1000

        if names is None:
            print(f"  SKIP  retrieval returned None for {model}")
            continue

        # Resolve to tool instances
        filtered = [tool_by_name[n] for n in names if n in tool_by_name]
        schemas = [tool_to_ollama_schema(t) for t in filtered]

        print(f"\n  Query: {prompt[:60]}...")
        print(f"  Retrieval: {len(filtered)} tools in {retrieval_ms:.1f}ms")
        print(f"  Expected tool: {expected} (in set: {expected in names})")

        # 2. LLM call
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant. Use tools to complete tasks. Do not explain, just call the tool."},
                {"role": "user", "content": prompt},
            ],
            "tools": schemas,
            "stream": False,
            "options": {"num_ctx": 4096},
        }

        t_llm = time.time()
        try:
            r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  LLM ERROR: {e}")
            continue
        llm_ms = (time.time() - t_llm) * 1000

        msg = data.get("message", {})
        tool_calls = msg.get("tool_calls", [])
        text_response = msg.get("content", "")

        if not tool_calls:
            print(f"  LLM: No tool call ({llm_ms:.0f}ms) — responded with text: {text_response[:80]}")
            continue

        tc_info = tool_calls[0]
        called_name = tc_info.get("function", {}).get("name", "?")
        called_args = tc_info.get("function", {}).get("arguments", {})
        correct = called_name == expected
        print(f"  LLM: called {called_name} ({llm_ms:.0f}ms) {'CORRECT' if correct else 'WRONG (expected ' + expected + ')'}")
        print(f"  Args: {json.dumps(called_args)[:120]}")

        # 3. Execute the tool
        tool = tool_by_name.get(called_name)
        if not tool:
            print(f"  EXEC: Tool {called_name} not found in loaded tools")
            continue

        t_exec = time.time()
        try:
            result = tool._run(**called_args)
            exec_ms = (time.time() - t_exec) * 1000
            result_str = str(result)[:200]
            print(f"  EXEC: {exec_ms:.0f}ms — {result_str}")
        except Exception as e:
            exec_ms = (time.time() - t_exec) * 1000
            print(f"  EXEC ERROR ({exec_ms:.0f}ms): {e}")

        total_ms = retrieval_ms + llm_ms + exec_ms
        print(f"  TOTAL: {total_ms:.0f}ms (retrieval={retrieval_ms:.0f} + llm={llm_ms:.0f} + exec={exec_ms:.0f})")

print(f"\n{'=' * 70}")
print("DONE")
