
import sys
import os
import inspect
import asyncio

# Add project root to path
sys.path.append(os.getcwd())

try:
    from distr.core.agent.tools.media.audio_transcriber import AudioTranscriberTool
except ImportError as e:
    print(f"❌ Failed to import AudioTranscriberTool: {e}")
    sys.exit(1)

def test_signature():
    print("Testing AudioTranscriberTool signature...")
    
    # Check _run signature
    sig_run = inspect.signature(AudioTranscriberTool._run)
    print(f"DEBUG: _run signature: {sig_run}")
    
    if 'last_user_message' in sig_run.parameters:
        print("✅ Success: _run accepts 'last_user_message'")
    else:
        print("❌ Failed: _run DOES NOT accept 'last_user_message'")
        
    if 'kwargs' in str(sig_run) or '**kwargs' in str(sig_run) or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_run.parameters.values()):
        print("✅ Success: _run accepts **kwargs")
    else:
        print("❌ Failed: _run DOES NOT accept **kwargs")

    # Check _arun signature
    sig_arun = inspect.signature(AudioTranscriberTool._arun)
    print(f"DEBUG: _arun signature: {sig_arun}")
    
    if 'last_user_message' in sig_arun.parameters:
        print("✅ Success: _arun accepts 'last_user_message'")
    else:
        print("❌ Failed: _arun DOES NOT accept 'last_user_message'")
        
    if 'kwargs' in str(sig_arun) or '**kwargs' in str(sig_arun) or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_arun.parameters.values()):
        print("✅ Success: _arun accepts **kwargs")
    else:
        print("❌ Failed: _arun DOES NOT accept **kwargs")

if __name__ == "__main__":
    test_signature()
