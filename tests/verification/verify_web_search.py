import sys
import os

# Add project root to path (two levels up from tests/verification/)
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
sys.path.insert(0, project_root)

from distr.core.agent.tools.web.web_search import WebSearchTool

def test_search():
    print("Testing WebSearchTool...")
    tool = WebSearchTool()
    try:
        result = tool._run("test query")
        print(f"Result: {result[:100]}...")
        if "not installed" in result:
            print("FAILURE: search packages not installed")
            sys.exit(1)
        else:
            print("SUCCESS: Search executed")
            sys.exit(0)
    except Exception as e:
        print(f"FAILURE: Exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_search()

