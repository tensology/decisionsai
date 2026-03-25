#!/usr/bin/env python3
"""
Interactive Fast Action Detector Demo

Run this script to test what the fast action detector does with various inputs.
Type commands and see whether they'll be handled as quick actions or sent to the LLM.

Usage:
    cd <project_root>
    python tests/agent/test_fast_detector_interactive.py
"""

import sys
import os

# Get project root (4 levels up from this file)
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(script_dir)))
sys.path.insert(0, project_root)

# Load the fast action detector by executing the file directly
fast_detector_path = os.path.join(project_root, 'distr', 'agent', 'distr', 'services', 'fast_action_detector.py')
exec(open(fast_detector_path).read())

def main():
    detector = FastActionDetector()
    
    print("\n" + "=" * 60)
    print("🎤  FAST ACTION DETECTOR - Interactive Demo")
    print("=" * 60)
    print("Type what you want to do and I'll tell you what happens.")
    print("Type 'quit' or 'q' to exit.\n")
    
    while True:
        try:
            user_input = input("🗣️  You: ").strip()
            
            if not user_input:
                continue
            
            if user_input.lower() in ['quit', 'q', 'exit']:
                print("\n👋 Goodbye!")
                break
            
            result = detector.detect(user_input)
            
            print()
            if result.confidence >= 0.9:
                print(f"   ⚡ FAST ACTION")
                print(f"   ├─ Tool: {result.tool_name}")
                print(f"   ├─ Args: {result.tool_args}")
                print(f"   ├─ Response type: {result.response_type}")
                if result.needs_copy_first:
                    print(f"   └─ 📋 Will copy selection first")
                else:
                    print(f"   └─ Confidence: {result.confidence}")
                    
                # Describe what happens
                response_descriptions = {
                    "done": "→ Just says 'Done' after executing",
                    "tts": "→ Reads content aloud via TTS",
                    "tts_clipboard": "→ Reads clipboard content aloud via TTS",
                    "llm_response": "→ LLM speaks the result conversationally"
                }
                desc = response_descriptions.get(result.response_type, "")
                if desc:
                    print(f"   {desc}")
                
                # Special note for web search
                if result.tool_name == "web_search":
                    print(f"   🔊 Plays search sound, then searches the web")
                    
            elif result.action_type == ActionType.CONVERSATIONAL:
                print(f"   🤖 → LLM (Conversational)")
                print(f"   └─ This looks like a question or chat request.")
                print(f"      The LLM will generate a natural response.")
            else:
                print(f"   🤖 → LLM (Needs context)")
                print(f"   └─ Confidence: {result.confidence}")
                print(f"      Routing to LLM for intelligent handling.")
            print()
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except EOFError:
            print("\n\n👋 Goodbye!")
            break

if __name__ == "__main__":
    main()

