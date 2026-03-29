"""Ensure the RAM-appropriate Ollama models are pulled. Called by decisions.bat."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from distr.core.system_resources import get_total_ram_gb, recommend_ollama_defaults

def main():
    try:
        import ollama
        ollama.list()  # test connection
    except Exception:
        print("Ollama not running — skipping model check")
        return

    ram = get_total_ram_gb()
    rec = recommend_ollama_defaults(ram)
    models_needed = [rec["conversational"], rec["coding"], rec["vision"]]

    # Get installed models
    try:
        installed = {m['name'] for m in ollama.list().get('models', [])}
    except Exception:
        installed = set()

    for model in models_needed:
        if model in installed or any(model in m for m in installed):
            print(f"  [ok] {model}")
        else:
            print(f"  Pulling {model}...")
            try:
                ollama.pull(model)
                print(f"  [ok] {model} pulled")
            except Exception as e:
                print(f"  [warn] Could not pull {model}: {e}")

if __name__ == "__main__":
    main()
