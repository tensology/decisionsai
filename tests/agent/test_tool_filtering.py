def test_tool_filtering():
    print("Testing dynamic tool filtering logic...")
    
    # Mock tools
    class MockTool:
        def __init__(self, name):
            self.name = name
            
    all_tools = [
        MockTool('mouse_movement'), MockTool('mouse_actions'),
        MockTool('text_editing'), MockTool('caret_movement'),
        MockTool('special_key'), MockTool('function_key'),
        MockTool('open_window'), MockTool('keyboard_shortcut'),
        MockTool('oracle_control'), MockTool('open_file_menu'),
        MockTool('media_control'),
        MockTool('clipboard_action'), MockTool('save_audio'),
        MockTool('rework_clipboard'), MockTool('summarize_clipboard'),
        MockTool('exit_app')
    ]
    
    def filter_tools(user_input):
        user_input = user_input.lower()
        relevant_tools = []
        
        # 1. Mouse Tools
        if any(kw in user_input for kw in ['mouse', 'click', 'scroll', 'drag', 'move']):
            relevant_tools.extend(['mouse_movement', 'mouse_actions'])
            
        # 2. Keyboard/Text Tools
        if any(kw in user_input for kw in ['type', 'press', 'enter', 'key', 'tab', 'escape', 'space', 'delete', 'backspace']):
            relevant_tools.extend(['text_editing', 'special_key', 'function_key', 'keyboard_shortcut'])
            
        # 3. Clipboard/Edit Tools
        if any(kw in user_input for kw in ['copy', 'cut', 'paste', 'clipboard', 'read', 'explain', 'elaborate', 'save', 'rework', 'summarize']):
            relevant_tools.extend(['clipboard_action', 'text_editing', 'save_audio', 'rework_clipboard', 'summarize_clipboard'])
            
        # 4. Window/App Tools
        if any(kw in user_input for kw in ['window', 'app', 'application', 'open', 'close', 'quit', 'exit', 'spotlight', 'menu']):
            relevant_tools.extend(['open_window', 'exit_app', 'open_file_menu', 'oracle_control', 'keyboard_shortcut'])
            
        # 5. Media Tools
        if any(kw in user_input for kw in ['play', 'pause', 'stop', 'volume', 'mute', 'media', 'music', 'song', 'track']):
            relevant_tools.extend(['media_control'])
            
        # If no specific keywords matched, return ALL tools (fallback)
        if not relevant_tools:
            return [t.name for t in all_tools]
            
        # Deduplicate and return
        return list(set(relevant_tools))

    # Test Cases
    cases = [
        ("move mouse to center", ['mouse_movement', 'mouse_actions']),
        ("click the button", ['mouse_movement', 'mouse_actions']),
        ("copy this text", ['clipboard_action', 'text_editing', 'save_audio', 'rework_clipboard', 'summarize_clipboard']),
        ("what's in my clipboard", ['clipboard_action', 'text_editing', 'save_audio', 'rework_clipboard', 'summarize_clipboard']),
        ("open chrome", ['open_window', 'exit_app', 'open_file_menu', 'oracle_control', 'keyboard_shortcut']),
        ("play music", ['media_control']),
        ("tell me a story", [t.name for t in all_tools]), # No keywords -> All tools
        ("press enter", ['text_editing', 'special_key', 'function_key', 'keyboard_shortcut']),
    ]
    
    for input_text, expected_subset in cases:
        filtered = filter_tools(input_text)
        print(f"Input: '{input_text}' -> Filtered: {filtered}")
        
        # Check if expected tools are present
        if expected_subset == [t.name for t in all_tools]:
            assert len(filtered) == len(all_tools), f"Expected all tools for '{input_text}'"
        else:
            # Check if at least the expected tools are in the filtered list
            # (It's okay if more are present due to overlapping keywords)
            missing = [t for t in expected_subset if t not in filtered]
            assert not missing, f"Missing tools {missing} for '{input_text}'"
            
            # Check that we filtered OUT irrelevant tools
            # e.g. for "move mouse", we shouldn't see "media_control"
            if "mouse" in input_text:
                assert "media_control" not in filtered, f"Leaked media_control for '{input_text}'"

    print("All tests passed!")

if __name__ == "__main__":
    test_tool_filtering()
