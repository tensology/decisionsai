"""Diagnose why semantic tool retrieval might not be filtering tools."""
import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

print("=" * 60)
print("SEMANTIC TOOL RETRIEVAL DIAGNOSTIC")
print("=" * 60)

# 1. Check if _tool_cache is populated
print("\n1. Tool cache status:")
from distr.core.agent.tools.loader import _tool_cache
print(f"   _tool_cache has {len(_tool_cache)} entries")
if _tool_cache:
    print(f"   First 5: {list(_tool_cache.keys())[:5]}")
else:
    print("   EMPTY — warm_tool_cache was never called")

# 2. Check retriever singleton state
print("\n2. Retriever singleton status:")
from distr.core.agent.tool_retriever import get_tool_retriever, ALWAYS_ON_NAMES
retriever = get_tool_retriever()
print(f"   is_ready(): {retriever.is_ready()}")
print(f"   _model: {retriever._model}")
print(f"   _names count: {len(retriever._names)}")
print(f"   _matrix: {retriever._matrix is not None}")

# 3. Check kill switch
print("\n3. Kill switch:")
env_val = os.environ.get("DECISIONS_TOOL_RETRIEVAL_ENABLED", "true")
print(f"   DECISIONS_TOOL_RETRIEVAL_ENABLED = {repr(env_val)}")
print(f"   Kill switch active: {env_val.lower() == 'false'}")

# 4. Try a retrieval
print("\n4. Test retrieval (query='tell me how many tools you have'):")
result = retriever.retrieve("tell me how many tools you have", "bytedance-seed/coig-seed-2.0-pro-free")
if result is None:
    print("   retrieve() returned None — fallback to all tools")
    print("   This means either kill switch is active or index is not ready")
else:
    print(f"   retrieve() returned {len(result)} tool names")
    print(f"   Names: {result}")

# 5. If index not ready, try building it manually
if not retriever.is_ready():
    print("\n5. Index not ready — attempting manual build...")
    from distr.core.agent.tools.loader import load_tools, TOOL_DESCRIPTIONS
    print(f"   TOOL_DESCRIPTIONS has {len(TOOL_DESCRIPTIONS)} entries")
    
    # Try loading tools to build index
    tools = load_tools(use_navigation_tools=True)
    print(f"   Loaded {len(tools)} tools")
    
    print("   Building index synchronously...")
    t0 = time.time()
    try:
        retriever.build_index(tools)
        elapsed = time.time() - t0
        print(f"   Index built in {elapsed:.1f}s — is_ready: {retriever.is_ready()}")
        
        # Retry retrieval
        result = retriever.retrieve("tell me how many tools you have", "bytedance-seed/coig-seed-2.0-pro-free")
        if result:
            print(f"   After build: retrieve() returned {len(result)} tools")
            print(f"   Names: {result}")
    except Exception as e:
        print(f"   Build failed: {e}")
else:
    print("\n5. Index is ready — skipping manual build")

# 6. Check _get_filtered_tools behavior
print("\n6. Simulating _get_filtered_tools logic:")
if retriever.is_ready():
    names = retriever.retrieve("tell me how many tools you have", "bytedance-seed/coig-seed-2.0-pro-free")
    if names is None:
        print("   Would return ALL tools (retrieve returned None)")
    else:
        print(f"   Would return {len(names)} retrieved tools + sticky injections")
        # Check how many would resolve from _tool_cache
        from distr.core.agent.tools.loader import get_cached_tool
        resolved = [n for n in names if get_cached_tool(n) is not None]
        missing = [n for n in names if get_cached_tool(n) is None]
        print(f"   Resolved from cache: {len(resolved)}")
        if missing:
            print(f"   MISSING from cache: {missing}")
else:
    print("   Index not ready — would return ALL tools")

print("\n" + "=" * 60)
print("DONE")
