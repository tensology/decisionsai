import re
import ast
import os

def load_clean_text_function():
    """Extract and compile the clean_text_for_tts function from the source file
    to avoid importing the entire package and its dependencies."""
    # Get the project root (two levels up from tests/verification/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    ollama_utils_path = os.path.join(project_root, 'distr', 'agent', 'distr', 'services', 'llm', 'utils.py')
    
    with open(ollama_utils_path, 'r') as f:
        source = f.read()
    
    # Parse the source code
    module_ast = ast.parse(source)
    
    # Find the function definition
    function_def = next(node for node in module_ast.body if isinstance(node, ast.FunctionDef) and node.name == 'clean_text_for_tts')
    
    # Create a wrapper module to compile it
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

    print("Running verification tests...")
    
    # Simulate valid LLM streaming output
    chunks = ["Here is ", "a simple ", "sentence."]
    
    reconstructed_full = ""
    reconstructed_cleaned = ""
    
    print(f"Original chunks: {chunks}")
    
    for chunk in chunks:
        # Pass strip_whitespace=False to mimic the fix in ollama.py
        cleaned = clean_text_for_tts(chunk, strip_whitespace=False)
        reconstructed_full += chunk
        reconstructed_cleaned += cleaned
        print(f"Chunk: '{chunk}' -> Cleaned: '{cleaned}' (strip_whitespace=False)")
        
    print("-" * 30)
    print(f"Full Original: '{reconstructed_full}'")
    print(f"Full Cleaned : '{reconstructed_cleaned}'")
    
    expected = "Here is a simple sentence."
    if reconstructed_cleaned == expected:
        print("PASS: Text preserved correctly.")
    else:
        print(f"FAIL: Text mismatch! Expected '{expected}', got '{reconstructed_cleaned}'")

    # Also verify default behavior (strip_whitespace=True) still works
    print("\nVerifying default behavior (strip_whitespace=True)...")
    text_with_spaces = "  padded text  "
    cleaned_default = clean_text_for_tts(text_with_spaces)
    print(f"Default: '{text_with_spaces}' -> '{cleaned_default}'")
    assert cleaned_default == "padded text", "Default behavior should strip spaces"
    print("PASS: Default behavior preserved.")

if __name__ == "__main__":
    verify_fix()
