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
    
    # Test case 1: Normal sentence
    text1 = "This is a test sentence."
    cleaned1 = clean_text_for_tts(text1)
    print(f"Test 1 (Normal): '{text1}' -> '{cleaned1}'")
    assert cleaned1 == text1
    
    # Test case 2: Sentence with newlines (The Fix)
    text2 = "This\nis\na\ntest."
    cleaned2 = clean_text_for_tts(text2)
    # Note: The function also does: text = re.sub(r'\n{3,}', '\n\n', text) and changes \r\n to \n
    # But single newlines should be preserved
    print(f"Test 2 (Newlines): {repr(text2)} -> {repr(cleaned2)}")
    
    # Check if newlines are inside the string
    if "\n" in cleaned2:
        print("PASS: Newlines preserved.")
    else:
        print("FAIL: Newlines stripped!")
        
    # Check specifically that it didn't become "Thisisatest." or "This is a test." (if spaces were there originally)
    # The input had NO spaces, only newlines. So if newlines are kept, it should match input (or close to it)
    # Wait, the function has this at the end:
    # if '\n' in text: lines = [line.strip() for line in lines]; text = '\n'.join(lines)
    # So "This\nis" -> "This\nis" (strip does nothing on "This")
    
    assert "\n" in cleaned2, "Newlines should be preserved"
    
    # Test case 3: User example approx
    text3 = "Alright,\nhere's\na\nquick\nstory."
    cleaned3 = clean_text_for_tts(text3)
    print(f"Test 3 (User Story): {repr(text3)} -> {repr(cleaned3)}")
    assert "\n" in cleaned3
    assert "Alright," in cleaned3
    
    # Test case 4: Tabs
    text4 = "Col1\tCol2"
    cleaned4 = clean_text_for_tts(text4)
    print(f"Test 4 (Tabs): {repr(text4)} -> {repr(cleaned4)}")
    assert "\t" in cleaned4 or " " in cleaned4 # Depending on how regex handles it? NO, we explicitly allowed 0x09
    # But wait, later in function: text = re.sub(r'[ \t]{2,}', ' ', text)
    # This collapses multiple tabs/spaces to single space. Single tab should remain if not adjacent to other space?
    # Actually [ \t]{2,} means 2 or more. Single tab is length 1. So it should stay.
    
    print("\nALL VERIFICATION TESTS PASSED!")

if __name__ == "__main__":
    verify_fix()
