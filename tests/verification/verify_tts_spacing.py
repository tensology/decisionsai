import re
import ast
import sys
import os

def load_clean_text_function():
    """Extract and compile the clean_text_for_tts function from the source file
    to avoid importing the entire package and its dependencies."""
    # Get the project root (two levels up from tests/verification/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    ollama_utils_path = os.path.join(project_root, 'distr', 'agent', 'distr', 'services', 'llm', 'utils.py')
    
    try:
        with open(ollama_utils_path, 'r') as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: File not found at {ollama_utils_path}")
        sys.exit(1)
    
    # Parse the source code
    try:
        module_ast = ast.parse(source)
    except SyntaxError as e:
        print(f"Error parsing {ollama_utils_path}: {e}")
        sys.exit(1)
    
    # Find the function definition
    try:
        function_def = next(node for node in module_ast.body if isinstance(node, ast.FunctionDef) and node.name == 'clean_text_for_tts')
    except StopIteration:
        print("Error: clean_text_for_tts function not found in file.")
        sys.exit(1)
    
    # Create a wrapper module to compile it
    # We must import 're' in the wrapper scope since the function uses it
    wrapper_module = ast.Module(body=[
        ast.Import(names=[ast.alias(name='re', asname=None)]),
        function_def
    ], type_ignores=[])
    
    # Fix AST locations
    ast.fix_missing_locations(wrapper_module)
    
    # Compile and execute definition
    code = compile(wrapper_module, filename="<string>", mode="exec")
    namespace = {}
    exec(code, namespace)
    
    return namespace['clean_text_for_tts']

def verify_fix():
    print("Loading clean_text_for_tts directly from source...")
    try:
        clean_text_for_tts = load_clean_text_function()
    except Exception as e:
        print(f"Failed to load function: {e}")
        return

    print("Running comprehensive streaming verification tests...")
    
    # helper for colored output
    def assert_eq(test_name, actual, expected):
        if actual == expected:
            print(f"✅ {test_name}: PASS ('{actual}')")
        else:
            print(f"❌ {test_name}: FAIL\n   Expected: '{expected}'\n   Actual:   '{actual}'")

    # Scenario 1: Streaming a sentence fragment by fragment
    # "The quick brown fox jumps over the lazy dog."
    chunks_1 = ["The ", "quick ", "brown ", "fox ", "jumps ", "over ", "the ", "lazy ", "dog."]
    processed_1 = ""
    for chunk in chunks_1:
        processed_1 += clean_text_for_tts(chunk, strip_whitespace=False)
    assert_eq("Scenario 1 (Basic Sentence)", processed_1, "The quick brown fox jumps over the lazy dog.")

    # Scenario 2: Splitting inside spaces
    # "Hello world" -> "Hello", " world"
    chunks_2 = ["Hello", " world"]
    processed_2 = ""
    for chunk in chunks_2:
        processed_2 += clean_text_for_tts(chunk, strip_whitespace=False)
    assert_eq("Scenario 2 (Split at space)", processed_2, "Hello world")

    # Scenario 3: Markdown and punctuation
    # "**Warning**: Please check!" -> "**Warning**", ": ", "Please ", "check!"
    chunks_3 = ["**Warning**", ": ", "Please ", "check!"]
    processed_3 = ""
    for chunk in chunks_3:
        # clean_text_for_tts removes asterisks, so we expect "Warning: Please check!"
        processed_3 += clean_text_for_tts(chunk, strip_whitespace=False)
    assert_eq("Scenario 3 (Markdown & Punctuation)", processed_3, "Warning: Please check!")

    # Scenario 4: Newlines and formatting
    # "Title\n\nBody text."
    chunks_4 = ["Title", "\n\n", "Body ", "text."]
    processed_4 = ""
    for chunk in chunks_4:
        processed_4 += clean_text_for_tts(chunk, strip_whitespace=False)
    assert_eq("Scenario 4 (Newlines)", processed_4, "Title\n\nBody text.")

    # Scenario 5: User Reported Issue (Sticky Text)
    # Simulating what actually happened: "Sounds", "like", "a", "plan!" getting stripped
    # If the bug were present, this would be "Soundslikeaplan!"
    chunks_5 = ["Sounds ", "like ", "a ", "plan!"]
    processed_5_fixed = ""
    for chunk in chunks_5:
        # With fix (strip_whitespace=False)
        processed_5_fixed += clean_text_for_tts(chunk, strip_whitespace=False)
    
    assert_eq("Scenario 5 (Sticky Text Fix Verification)", processed_5_fixed, "Sounds like a plan!")

    # Test the BUG (Regression test)
    # verify that strip_whitespace=True (default) WOULD cause the issue
    processed_5_bug = ""
    for chunk in chunks_5:
        processed_5_bug += clean_text_for_tts(chunk, strip_whitespace=True)
    
    if processed_5_bug == "Soundslikeaplan!":
        print("✅ Regression Test: Default behavior correctly reproduces the bug (strip_whitespace=True)")
    else:
        print(f"❌ Regression Test: Failed to reproduce bug? Got: '{processed_5_bug}'")

    # Scenario 6: Code-like / Technical text
    # "function(arg1, arg2)"
    chunks_6 = ["function(", "arg1, ", "arg2)"]
    processed_6 = ""
    for chunk in chunks_6:
        processed_6 += clean_text_for_tts(chunk, strip_whitespace=False)
    assert_eq("Scenario 6 (Technical Text)", processed_6, "function(arg1, arg2)")

    # Test complete
    print("\nVerification complete.")

if __name__ == "__main__":
    verify_fix()

