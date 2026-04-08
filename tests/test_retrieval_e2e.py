"""
End-to-end retrieval test against live Ollama models.

Tests:
1. Index build time
2. Retrieval latency per query
3. Whether obscure tools are reachable via RequestToolTool
4. Micro-tier models get only 6 tools
5. Standard-tier models get ≤17 tools
"""
import sys, os, time, json, requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ── Discover Ollama models ──
def get_ollama_models():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []

# ── Load tools and build index ──
print("=" * 70)
print("SEMANTIC TOOL RETRIEVAL — END-TO-END TEST")
print("=" * 70)

from distr.core.agent.tools.loader import load_tools, TOOL_DESCRIPTIONS
from distr.core.agent.tool_retriever import ToolRetriever, ALWAYS_ON_NAMES

tools = load_tools(use_navigation_tools=True)
tool_names = [t.name for t in tools]
print(f"\nLoaded {len(tools)} tools")

retriever = ToolRetriever()
t0 = time.time()
retriever.build_index(tools)
build_ms = (time.time() - t0) * 1000
print(f"Index built in {build_ms:.0f}ms (backend: {retriever._backend})")
print(f"Index ready: {retriever.is_ready()}")

if not retriever.is_ready():
    print("FATAL: Index not ready — cannot proceed")
    sys.exit(1)

# ── Test queries: mix of common and obscure ──
TEST_QUERIES = [
    # Common
    ("open chrome", "smart_open"),
    ("take a screenshot", "screenshot_analyzer"),
    ("search the web for python", "web_search"),
    ("what files are on my desktop", "file_operations"),
    ("play the next song", "media_control"),
    # Obscure — tools that might not be in top-10
    ("transcribe this audio recording", "audio_transcriber"),
    ("upload this document to google drive", "upload_doc_to_google"),
    ("convert this markdown to a PDF", "convert_document"),
    ("send a voice note on telegram", "send_voice_note_to_telegram"),
    ("create a kanban ticket for this bug", "create_ticket"),
    ("generate an image of a cat", "image_generator"),
    ("run this python script", "execute_code"),
    ("start recording a macro", "start_recording"),
    ("what git changes have I made", "git_operations"),
    ("index the folder I just dropped", "index_folder"),
    ("wake up the computer", "wake_up"),
    ("rework my clipboard text", "rework_clipboard"),
]

# ── Retrieval tests ──
print(f"\n{'─' * 70}")
print("RETRIEVAL TESTS (K=10, standard tier)")
print(f"{'─' * 70}")

passed = 0
failed = 0
latencies = []

for query, expected_tool in TEST_QUERIES:
    t0 = time.time()
    result = retriever.retrieve(query, "llama3:8b", k=10)
    lat_ms = (time.time() - t0) * 1000
    latencies.append(lat_ms)

    found = expected_tool in result if result else False
    status = "PASS" if found else "FAIL"
    count = len(result) if result else 0

    if found:
        passed += 1
        print(f"  {status} ({lat_ms:5.1f}ms, {count:2d} tools) {query[:45]:<45} -> {expected_tool}")
    else:
        failed += 1
        # Check if tool exists at all
        exists = expected_tool in tool_names
        print(f"  {status} ({lat_ms:5.1f}ms, {count:2d} tools) {query[:45]:<45} -> MISSING {expected_tool} (exists={exists})")
        if result:
            print(f"        Got: {result[:8]}...")

print(f"\n  {passed}/{passed+failed} passed")
print(f"  Latency: avg={sum(latencies)/len(latencies):.1f}ms, "
      f"min={min(latencies):.1f}ms, max={max(latencies):.1f}ms")

# ── RequestToolTool reachability: can ANY tool be found? ──
print(f"\n{'─' * 70}")
print("REQUESTTOOLTOOL REACHABILITY (can obscure tools be fuzzy-matched?)")
print(f"{'─' * 70}")

try:
    from fuzzywuzzy import fuzz
except ImportError:
    from thefuzz import fuzz

from distr.core.agent.tools.loader import TOOL_REGISTRY

# Pick some tools that are unlikely to be in top-10 retrieval
OBSCURE_QUERIES = [
    ("I need the playwright browser automation", "PlaywrightTool"),
    ("rube workflow integration", "RubeTool"),
    ("speak on my desktop speakers", "SpeakOnDesktopTool"),
    ("PDF page extractor", "PDFPageExtractorTool"),
    ("vision analyzer for images", "VisionAnalyzerTool"),
]

for query, expected_class in OBSCURE_QUERIES:
    scores = []
    for class_name in TOOL_REGISTRY:
        name_score = fuzz.token_set_ratio(query.lower(), class_name.lower())
        desc = TOOL_DESCRIPTIONS.get(class_name, "")
        desc_score = fuzz.token_set_ratio(query.lower(), desc.lower()) if desc else 0
        scores.append((class_name, max(name_score, desc_score)))
    scores.sort(key=lambda x: x[1], reverse=True)
    top = scores[0]
    found = top[0] == expected_class
    status = "PASS" if found else "FAIL"
    print(f"  {status} {query[:50]:<50} -> {top[0]} (score={top[1]})")
    if not found:
        # Show where expected landed
        rank = next((i+1 for i, (n, _) in enumerate(scores) if n == expected_class), "?")
        exp_score = next((s for n, s in scores if n == expected_class), 0)
        print(f"        Expected {expected_class} at rank {rank} (score={exp_score})")

# ── Micro-tier model tests ──
print(f"\n{'─' * 70}")
print("MICRO-TIER TESTS")
print(f"{'─' * 70}")

micro_models = ["smollm:135m", "qwen3:0.6b", "tinyllama:1.1b", "phi-1.5:latest"]
for model in micro_models:
    tier = ToolRetriever.classify_model_tier(model)
    result = retriever.retrieve("open chrome", model)
    count = len(result) if result else 0
    is_micro = tier == "micro"
    has_only_6 = result is not None and set(result) == ALWAYS_ON_NAMES
    status = "PASS" if (is_micro and has_only_6) else "FAIL"
    print(f"  {status} {model:<25} tier={tier:<10} tools={count}")

# ── Ollama live models ──
models = get_ollama_models()
if models:
    print(f"\n{'─' * 70}")
    print(f"OLLAMA LIVE MODELS ({len(models)} found)")
    print(f"{'─' * 70}")
    for model in models:
        tier = ToolRetriever.classify_model_tier(model)
        t0 = time.time()
        result = retriever.retrieve("search the web for news", model, k=10)
        lat_ms = (time.time() - t0) * 1000
        count = len(result) if result else 0
        print(f"  {model:<40} tier={tier:<10} tools={count:2d}  ({lat_ms:.1f}ms)")
else:
    print("\nOllama not running — skipping live model tests")

print(f"\n{'=' * 70}")
print("DONE")
