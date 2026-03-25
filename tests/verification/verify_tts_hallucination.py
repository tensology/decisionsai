import re
import ast
import sys
import os

def load_clean_text_function():
    """Extract and compile the clean_text_for_tts function from the source file
    to avoid importing the entire package and its dependencies."""
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(_script_dir, '..', '..', 'distr', 'agent', 'distr', 'services', 'llm', 'utils.py')
    file_path = os.path.normpath(file_path)
    try:
        with open(file_path, 'r') as f:
            source = f.read()
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
        sys.exit(1)
    
    # Parse the source code
    try:
        module_ast = ast.parse(source)
    except SyntaxError as e:
        print(f"Error parsing {file_path}: {e}")
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

    print("Running hallucination cleaning tests...")
    
    # helper for colored output
    def assert_result(test_name, input_text, expected_output):
        cleaned = clean_text_for_tts(input_text, strip_whitespace=False)
        # Simplify comparison by stripping whitespace for the check, 
        # but the cleaning logic needs to handle the removal of the hallucination regardless of whitespace
        if cleaned.strip() == expected_output.strip():
            print(f"✅ {test_name}: PASS")
        else:
            print(f"❌ {test_name}: FAIL\n   Input:    '{input_text}'\n   Expected: '{expected_output}'\n   Actual:   '{cleaned}'")

    # Scenario: The exact leak user reported
    # Note: text has 'of15' typo from user report, we check if the PREAMBLE is removed
    leak_case = "Your tool output was: Created file: vegetables.txtYour response should be: Here is the list of15 vegetables."
    expected_leak = "Here is the list of15 vegetables."
    assert_result("Prompt Leakage 1", leak_case, expected_leak)

    # Scenario: Variation with spaces
    leak_case_2 = "Your tool output was: Some Result. Your response should be: The actual answer."
    expected_leak_2 = "The actual answer."
    assert_result("Prompt Leakage 2 (with spaces)", leak_case_2, expected_leak_2)

    # Scenario: Just tool output prefix at start
    leak_case_3 = "Your response should be: The answer."
    expected_leak_3 = "The answer."
    assert_result("Prompt Leakage 3 (Start)", leak_case_3, expected_leak_3)

    # Scenario: "Here's the natural response:" leakage
    leak_case_4 = "Here's the natural response: Sure, I can help with that."
    expected_leak_4 = "Sure, I can help with that."
    assert_result("Prompt Leakage 4 (Natural Response)", leak_case_4, expected_leak_4)

    print("\n--- Regression Tests (Spacing) ---")
    
    # Scenario 5: User Reported Issue (Sticky Text) - Regression Check
    # Simulating what actually happened: "Sounds", "like", "a", "plan!" getting stripped
    chunks_5 = ["Sounds ", "like ", "a ", "plan!"]
    processed_5_fixed = ""
    for chunk in chunks_5:
        # With fix (strip_whitespace=False)
        processed_5_fixed += clean_text_for_tts(chunk, strip_whitespace=False)
    
    if processed_5_fixed == "Sounds like a plan!":
         print(f"✅ Regression Test (Spacing): PASS ('{processed_5_fixed}')")
    else:
         print(f"❌ Regression Test (Spacing): FAIL\n   Expected: 'Sounds like a plan!'\n   Actual:   '{processed_5_fixed}'")

    print("\nVerification complete.")

if __name__ == "__main__":
    verify_fix()
