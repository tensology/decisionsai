from datetime import datetime, timedelta
from tqdm import tqdm
import subprocess
import requests
import warnings
import zipfile
import logging
import ollama
import sys
import os
import argparse


# Suppress specific warnings
warnings.filterwarnings("ignore", category=FutureWarning)

# Set logging level to suppress less important messages
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

def download_file(url, filename):
    """
    Download a file from the given URL and save it with the specified filename.
    """
    response = requests.get(url, stream=True)
    total_size = int(response.headers.get('content-length', 0))

    with open(filename, 'wb') as file, tqdm(
        desc=filename,
        total=total_size,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as progress_bar:
        for data in response.iter_content(chunk_size=1024):
            size = file.write(data)
            progress_bar.update(size)

def extract_zip(zip_path, extract_to):
    """
    Extract a zip file to the specified directory.
    """
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

def print_manual_pull_instructions(model_name):
    """Print instructions for manually pulling the model"""
    print("")
    print("=" * 60)
    print("MANUAL MODEL DOWNLOAD INSTRUCTIONS")
    print("=" * 60)
    print("")
    print("To manually download the model, run one of the following commands:")
    print("")
    print("  Option 1: Using Ollama CLI (recommended):")
    print(f"    ollama pull {model_name}")
    print("")
    print("  Option 2: Using Python:")
    print(f"    python -c \"import ollama; ollama.pull('{model_name}')\"")
    print("")
    print("  Option 3: Check if model is already installed:")
    print("    ollama list")
    print("")
    print("The model will download with progress indicators.")
    print("Once complete, you can run this setup script again to continue.")
    print("")
    print("=" * 60)
    print("")

def install_optional_dependencies():
    """
    Install optional dependencies (LlamaIndex).
    Handles Python 3.13 compatibility issues with tiktoken.
    """
    import subprocess
    import sys
    
    print("Installing optional dependencies (LlamaIndex)...")
    print("Note: If you're using Python 3.13, this may require PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1")
    print("")
    
    # Check Python version
    python_version = sys.version_info
    if python_version.major == 3 and python_version.minor >= 13:
        print("Detected Python 3.13+. Setting PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 for compatibility...")
        os.environ['PYO3_USE_ABI3_FORWARD_COMPATIBILITY'] = '1'
    
    try:
        # Install optional packages
        packages = [
            'llama-index>=0.10.0',
            'llama-index-embeddings-ollama>=0.1.0',
            'llama-index-llms-ollama>=0.1.0'
        ]
        
        for package in packages:
            print(f"Installing {package}...")
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', package],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print(f"✓ {package} installed successfully")
            else:
                print(f"⚠ Warning: Failed to install {package}")
                print(f"  Error: {result.stderr}")
                print(f"  You may need to install manually: pip install {package}")
                if python_version.major == 3 and python_version.minor >= 13:
                    print(f"  Or set environment variable: PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 pip install {package}")
        
        print("")
        print("Optional dependencies installation complete.")
        print("")
    except Exception as e:
        print(f"⚠ Warning: Error installing optional dependencies: {e}")
        print("  You can install them manually later:")
        print("  pip install llama-index llama-index-embeddings-ollama llama-index-llms-ollama")
        print("")

def _download_hf_model(repo_id: str, local_dir: str, label: str = "model"):
    """Download a HuggingFace model to a local directory (shared helper)."""
    print(f"  {label}")
    print(f"    Repo  : {repo_id}")
    print(f"    Local : {local_dir}")

    if os.path.isfile(os.path.join(local_dir, 'config.json')):
        print(f"    ✓ Already present")
        return True

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(f"    ⚠  huggingface_hub not installed — will download on first use.")
        return False

    print(f"    Downloading ...")
    try:
        os.makedirs(local_dir, exist_ok=True)
        snapshot_download(repo_id=repo_id, local_dir=local_dir)
        print(f"    ✓ Saved")
        return True
    except Exception as e:
        print(f"    ✗ Download failed: {e}")
        print(f"      Will download automatically on first use (into HF cache).")
        return False




def _prefetch_decisions_local_stt_caches() -> None:
    """Run ``scripts/prefetch_local_models.py`` so STT behaves like Whisper (weights cached before first mic)."""
    if (os.environ.get("DECISIONS_AI_SKIP_MODEL_PREFETCH") or "").strip() == "1":
        print("")
        print("Skipping local STT/TTS prefetch (DECISIONS_AI_SKIP_MODEL_PREFETCH=1).")
        return
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    script = os.path.join(root, "scripts", "prefetch_local_models.py")
    if not os.path.isfile(script):
        print("")
        print(f"⚠ prefetch script missing: {script}")
        return
    print("")
    print("=" * 60)
    print("Local STT / TTS model prefetch (Vosk, Whisper)")
    print("=" * 60)
    try:
        r = subprocess.run(
            [sys.executable, script, "--only", "all"],
            cwd=root,
            timeout=7200,
        )
        if r.returncode != 0:
            print(f"⚠ Prefetch exited with code {r.returncode} — first STT/TTS use may still download.")
        else:
            print("✓ Local STT/TTS prefetch finished.")
    except subprocess.TimeoutExpired:
        print("⚠ Local STT/TTS prefetch timed out — run manually: python scripts/prefetch_local_models.py")
    except Exception as e:
        print(f"⚠ Local STT/TTS prefetch failed: {e}")


def setup_kanade_models():
    """Pre-download Kanade voice cloning model and WavLM checkpoint.

    Downloads:
      1. frothywater/kanade-12.5hz from HuggingFace (voice conversion model)
      2. wavlm_base_plus.pth from PyTorch hub (speaker embedding extractor)

    Both are cached in their default locations (~/.cache/huggingface and
    ~/.cache/torch/hub/checkpoints) so they're available instantly when a
    user first plays a custom Kokoro voice.
    """
    print("")
    print("=" * 60)
    print("Kanade Voice Cloning Model Setup")
    print("=" * 60)
    print("")

    # 1. Kanade model via HuggingFace
    print("  Kanade voice conversion model (frothywater/kanade-12.5hz)")
    try:
        from huggingface_hub import snapshot_download
        print("    Downloading ...")
        snapshot_download(repo_id="frothywater/kanade-12.5hz")
        print("    ✓ Cached")
    except ImportError:
        print("    ⚠  huggingface_hub not installed — will download on first use.")
    except Exception as e:
        print(f"    ✗ Download failed: {e}")
        print("      Will download automatically on first use.")

    # 2. WavLM checkpoint via torch hub
    print("  WavLM base plus (speaker embedding extractor)")
    try:
        import torch
        wavlm_path = os.path.join(torch.hub.get_dir(), "checkpoints", "wavlm_base_plus.pth")
        if os.path.exists(wavlm_path):
            print("    ✓ Already cached")
        else:
            print("    Downloading ...")
            torch.hub.download_url_to_file(
                "https://download.pytorch.org/torchaudio/models/wavlm_base_plus.pth",
                wavlm_path,
            )
            print("    ✓ Cached")
    except Exception as e:
        print(f"    ✗ Download failed: {e}")
        print("      Will download automatically on first use.")
    print("")


def _model_display_name(model_id: str) -> str:
    """Convert a model ID like 'minimax-m2.5:cloud' to a display name like 'MiniMax M2.5'."""
    name = model_id.split(":")[0] if ":" in model_id else model_id
    display_names = {
        "minimax-m2.5": "MiniMax M2.5",
        "glm-5.1": "GLM 5.1",
        "qwen3.5": "Qwen 3.5 397B",
        "qwen3-coder-next": "Qwen3 Coder Next",
        "qwen3-vl": "Qwen3 VL",
        "qwen3-vl:235b-cloud": "Qwen3 VL",
        "qwen3": "Qwen3",
        "qwen2.5-coder": "Qwen2.5 Coder",
        "x/flux2-klein": "Flux2 Klein",
    }
    return display_names.get(name, name.replace("-", " ").title())


def setup_pi_cli(ram_gb=None, rec=None):
    """Configure pi CLI's models.json with Ollama cloud models.

    Creates ~/.pi/agent/models.json so that pi CLI (the coding agent)
    can use the same Ollama cloud models that DecisionsAI uses.
    This is idempotent — it merges with any existing config.
    """
    import json

    pi_dir = os.path.expanduser("~/.pi/agent")
    models_path = os.path.join(pi_dir, "models.json")

    # Determine which models to configure
    if rec is None:
        try:
            from distr.core.system_resources import recommend_ollama_defaults
            rec = recommend_ollama_defaults(ram_gb)
        except Exception:
            rec = {"conversational": "minimax-m2.5:cloud", "coding": "glm-5.1:cloud", "vision": "qwen3-vl:235b-cloud"}

    conv_model = rec["conversational"]
    code_model = rec["coding"]

    print("")
    print("=" * 60)
    print("pi CLI Setup (Coding Agent)")
    print("=" * 60)

    # Build the config
    pi_config = {
        "providers": {
            "ollama": {
                "baseUrl": "http://127.0.0.1:11434/v1",
                "apiKey": "ollama",
                "api": "openai-completions",
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": True,
                    "reasoningEffortMap": {
                        "off": "none",
                        "minimal": "none",
                        "low": "none",
                        "medium": "none",
                        "high": "none",
                        "xhigh": "none"
                    }
                },
                "models": [
                    {"id": conv_model, "name": _model_display_name(conv_model)},
                    {"id": code_model, "name": _model_display_name(code_model)},
                    {"id": "qwen3-coder-next:cloud", "name": "Qwen3 Coder Next"},
                    {"id": "qwen3.5:397b-cloud", "name": "Qwen 3.5 397B"},
                    {"id": "minimax-m2.5:cloud", "name": "MiniMax M2.5"},
                    {"id": "glm-5.1:cloud", "name": "GLM 5.1"},
                    {"id": "qwen3-vl:235b-cloud", "name": "Qwen3 VL"},
                ]
            }
        }
    }

    # --- Step 1: Install pi CLI if not present ---
    pi_installed = False
    try:
        result = subprocess.run(["pi", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            pi_installed = True
            print("  ✓ pi CLI is already installed")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if not pi_installed:
        print("  pi CLI is not installed. Attempting automatic install...")
        npm_available = False
        try:
            npm_check = subprocess.run(["npm", "--version"], capture_output=True, text=True, timeout=5)
            if npm_check.returncode == 0:
                npm_available = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        if npm_available:
            try:
                result = subprocess.run(
                    ["npm", "install", "-g", "@mariozechner/pi-coding-agent"],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    print("  ✓ pi CLI installed successfully!")
                    pi_installed = True
                else:
                    print(f"  ✗ npm install failed: {result.stderr[:200]}")
                    print("    Install manually: npm install -g @mariozechner/pi-coding-agent")
            except subprocess.TimeoutExpired:
                print("  ✗ npm install timed out (120s)")
                print("    Install manually: npm install -g @mariozechner/pi-coding-agent")
            except Exception as e:
                print(f"  ✗ Could not auto-install: {e}")
                print("    Install manually: npm install -g @mariozechner/pi-coding-agent")
        else:
            print("  ⚠ npm not found. Install Node.js first: https://nodejs.org")
            print("    Then run: npm install -g @mariozechner/pi-coding-agent")

    # --- Step 2: Write/merge models.json ---
    # This works regardless of whether pi is installed — the config will be ready
    # when the user first launches pi.
    existing_config = {}
    if os.path.exists(models_path):
        try:
            with open(models_path, "r") as f:
                existing_config = json.load(f)
            print(f"  Found existing pi config at {models_path}")
        except (json.JSONDecodeError, IOError) as e:
            print(f"  ⚠ Could not read existing models.json: {e}")
            existing_config = {}

    if existing_config:
        # Merge ollama models into existing config (preserves other providers)
        if "providers" not in existing_config:
            existing_config["providers"] = {}
        if "ollama" not in existing_config["providers"]:
            existing_config["providers"]["ollama"] = pi_config["providers"]["ollama"]
        else:
            # Merge models — add any that don't already exist by id
            existing_ids = {m["id"] for m in existing_config["providers"]["ollama"].get("models", [])}
            new_models = pi_config["providers"]["ollama"]["models"]
            for m in new_models:
                if m["id"] not in existing_ids:
                    existing_config["providers"]["ollama"].setdefault("models", []).append(m)
            # Ensure compat settings are present
            if "compat" not in existing_config["providers"]["ollama"]:
                existing_config["providers"]["ollama"]["compat"] = pi_config["providers"]["ollama"]["compat"]
            # Ensure baseUrl and apiKey
            if "baseUrl" not in existing_config["providers"]["ollama"]:
                existing_config["providers"]["ollama"]["baseUrl"] = pi_config["providers"]["ollama"]["baseUrl"]
            if "apiKey" not in existing_config["providers"]["ollama"]:
                existing_config["providers"]["ollama"]["apiKey"] = pi_config["providers"]["ollama"]["apiKey"]
            if "api" not in existing_config["providers"]["ollama"]:
                existing_config["providers"]["ollama"]["api"] = pi_config["providers"]["ollama"]["api"]
        merged_config = existing_config
    else:
        merged_config = pi_config

    # Write the config
    os.makedirs(pi_dir, exist_ok=True)
    try:
        with open(models_path, "w") as f:
            json.dump(merged_config, f, indent=2)
            f.write("\n")  # trailing newline
        model_count = len(merged_config.get("providers", {}).get("ollama", {}).get("models", []))
        print(f"  ✓ pi CLI config written to {models_path}")
        print(f"    Conversational: {conv_model}")
        print(f"    Coding:         {code_model}")
        print(f"    Total Ollama models: {model_count}")
    except IOError as e:
        print(f"  ✗ Could not write {models_path}: {e}")
        print(f"    Create it manually with the content above.")

    print("")


def setup(skip_model_pull=False, install_optional=False):
    """
    Main setup function to download and extract files.

    Args:
        skip_model_pull: If True, skip automatic model pull and show manual instructions
        install_optional: If True, install optional dependencies (LlamaIndex)
    """
    # Install optional dependencies if requested
    if install_optional:
        install_optional_dependencies()
    
    # Create the models directory if it doesn't exist
    os.makedirs('./distr/core/agent/models', exist_ok=True)

    # Define kokoro model files and URLs
    kokoro_files = {
        'model': {
            'url': 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx',
            'filename': './distr/core/agent/models/kokoro-v1.0.onnx'
        },
        'voices': {
            'url': 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin',
            'filename': './distr/core/agent/models/voices-v1.0.bin'
        }
    }

    # Download kokoro model files if they don't exist
    print("Setting up Kokoro TTS model...")
    for file_info in kokoro_files.values():
        if not os.path.exists(file_info['filename']):
            print(f"Downloading {os.path.basename(file_info['filename'])}...")
            download_file(file_info['url'], file_info['filename'])
        else:
            print(f"{os.path.basename(file_info['filename'])} already exists. Skipping download.")

    print("Setting up Ollama models...")

    # Detect system RAM and pick appropriately-sized models
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from distr.core.system_resources import get_total_ram_gb, recommend_ollama_defaults
        ram_gb = get_total_ram_gb()
        rec = recommend_ollama_defaults(ram_gb)
        print(f"Detected {ram_gb:.0f} GB RAM — selecting models accordingly")
    except Exception as _e:
        print(f"Could not detect RAM ({_e}), using defaults for 16 GB")
        ram_gb = 16.0
        rec = {"conversational": "deepseek-v4-pro:cloud", "coding": "glm-5.1:cloud", "vision": "qwen3-vl:235b-cloud"}

    # Write recommended models to a file so the DB can pick them up on first creation
    try:
        import json
        os.makedirs('installer', exist_ok=True)
        with open(os.path.join('installer', '.model_defaults.json'), 'w') as f:
            json.dump(rec, f)
        print(f"Saved model defaults: {rec}")
    except Exception as _e:
        print(f"Warning: could not save model defaults: {_e}")

    default_models = [
        (rec["conversational"], "Conversational LLM"),
        (rec["coding"],         "Coding LLM"),
        (rec["vision"],         "Vision LLM"),
    ]
    # Only pull the large image model on machines with plenty of RAM
    if ram_gb >= 16:
        default_models.append(("x/flux2-klein:latest", "Image Generation"))

    # First, check if Ollama is running and accessible
    try:
        print("Checking Ollama connection...")
        ollama.list()  # Test connection
        print("✓ Ollama is running")
    except Exception as e:
        print(f"✗ Error connecting to Ollama: {e}")
        print("")
        print("Please ensure Ollama is installed and running:")
        print("  1. Install Ollama from https://ollama.ai")
        print("  2. Start Ollama service")
        print("  3. Run this setup script again")
        print("")
        print("Skipping Ollama model setup. Everything else is ready.")
        return

    def check_model_status(model_name):
        try:
            models = ollama.list()
            for model in models['models']:
                if model['name'] == model_name:
                    modified_time = datetime.strptime(model['modified'], "%Y-%m-%dT%H:%M:%S.%fZ")
                    return datetime.utcnow() - modified_time < timedelta(days=1)
            return False
        except Exception:
            return False

    # Check if user wants to skip automatic pull
    if skip_model_pull:
        for model_name, label in default_models:
            print(f"Skipping automatic model pull for {model_name} ({label})")
            print_manual_pull_instructions(model_name)
    else:
        for model_name, label in default_models:
            if not check_model_status(model_name):
                print(f"Pulling {label}: {model_name}...")
                print("This may take a while depending on your internet connection.")
                print("")

                # Pull model with progress indication
                current_progress = None
                current_layer = None
                manifest_started = False
                last_update_time = datetime.now()

                try:
                    print("Downloading model manifest (usually takes 1-5 seconds)...", end='', flush=True)

                    for progress in ollama.pull(model_name, stream=True):
                        last_update_time = datetime.now()

                        if 'status' in progress:
                            status = progress['status']

                            if status == 'pulling manifest':
                                if not manifest_started:
                                    manifest_started = True
                                    print("")  # New line after manifest message
                                    print("Downloading model manifest...", end='', flush=True)
                            elif status == 'downloading':
                                if manifest_started:
                                    print("")  # New line after manifest completes
                                    manifest_started = False

                                if 'digest' in progress:
                                    layer_digest = progress['digest'][:12]  # Short hash
                                    if layer_digest != current_layer:
                                        if current_progress:
                                            current_progress.close()
                                        current_layer = layer_digest
                                        current_progress = tqdm(
                                            desc=f"Downloading layer {layer_digest}",
                                            unit='iB',
                                            unit_scale=True,
                                            unit_divisor=1024,
                                            leave=False
                                        )

                                    if 'total' in progress and 'completed' in progress:
                                        total = progress['total']
                                        completed = progress['completed']
                                        if current_progress:
                                            current_progress.total = total
                                            current_progress.n = completed
                                            current_progress.refresh()
                            elif status == 'verifying sha256 digest':
                                if current_progress:
                                    current_progress.set_description("Verifying layer integrity")
                                    current_progress.refresh()
                            elif status == 'writing manifest':
                                if current_progress:
                                    current_progress.close()
                                    current_progress = None
                                print("\nWriting model manifest...")
                            elif status == 'success':
                                if current_progress:
                                    current_progress.close()
                                print(f"✓ {label}: {model_name} downloaded successfully!")
                                print("")
                                break

                        # Check for timeout (if no progress for 30 seconds, show warning)
                        if (datetime.now() - last_update_time).total_seconds() > 30:
                            print("\n⚠ Warning: No progress update for 30 seconds. This might indicate a network issue.")
                            print("   The download will continue, but you may want to check your internet connection.")
                            print("   If it continues to hang, press Ctrl+C and run with --skip-model-pull to do it manually.")
                            last_update_time = datetime.now()  # Reset timer

                except KeyboardInterrupt:
                    if current_progress:
                        current_progress.close()
                    print("\n\nDownload interrupted by user.")
                    print("")
                    print("To download the remaining models manually, run:")
                    for mn, _, _ in default_models:
                        print(f"  ollama pull {mn}")
                    print("")
                    raise
                except Exception as e:
                    if current_progress:
                        current_progress.close()
                    print(f"\n✗ Error pulling {model_name}: {e}")
                    print(f"  You can pull it manually later: ollama pull {model_name}")
                    print("")
                    # Continue with next model instead of failing entirely
                    continue
            else:
                print(f"✓ {label}: {model_name} is already installed and up to date.")

    os.makedirs("assets/tmp", exist_ok=True)

    # Install Playwright browsers (chromium only — keeps install small)
    print("")
    print("Setting up Playwright (headless browser)...")
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'playwright', 'install', 'chromium'],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print("✓ Playwright Chromium browser installed")
        else:
            print(f"⚠ Playwright install returned code {result.returncode}")
            if result.stderr:
                print(f"  {result.stderr.strip()[:200]}")
            print("  You can install manually: python -m playwright install chromium")
    except FileNotFoundError:
        print("⚠ Playwright not found. Install with: pip install playwright && playwright install chromium")
    except subprocess.TimeoutExpired:
        print("⚠ Playwright install timed out. Run manually: python -m playwright install chromium")
    except Exception as e:
        print(f"⚠ Playwright setup error: {e}")
        print("  You can install manually: python -m playwright install chromium")

    # Pre-download Kanade voice cloning models (avoids long wait on first custom voice play)
    setup_kanade_models()

    # Local STT/TTS (Vosk tree, Whisper gguf warm)
    _prefetch_decisions_local_stt_caches()

    # --- pi CLI Setup ---
    setup_pi_cli(ram_gb, rec)

    # --- ECC Harness Pack Setup ---
    print("")
    print("=" * 60)
    print("ECC Harness Pack Setup")
    print("=" * 60)
    try:
        from distr.core.harness_pack import ensure_harness_pack_setup

        result = ensure_harness_pack_setup(run_full=True)
        detected = ", ".join(
            name for name, present in result.get("detected", {}).items() if present
        ) or "none"
        print(f"  Vendor ready: {bool(result.get('vendor_ready'))}")
        print(f"  Detected harnesses: {detected}")
        print(f"  Registry cache: {result.get('registry_path')}")
        written = result.get("written") or []
        if written:
            print(f"  Updated projections: {len(written)}")
        else:
            print("  Projections already current.")
    except Exception as e:
        print(f"  Warning: ECC harness pack setup skipped: {e}")

    print("All models have been downloaded and set up successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Setup DecisionsAI models and dependencies')
    parser.add_argument(
        '--skip-model-pull',
        action='store_true',
        help='Skip automatic Ollama model pull and show manual instructions instead'
    )
    parser.add_argument(
        '--pull-models',
        action='store_true',
        help='Opt in to automatic Ollama model pulls during setup (disabled by default)'
    )
    parser.add_argument(
        '--manual-model',
        action='store_true',
        help='Alias for --skip-model-pull (shows manual pull instructions)'
    )
    parser.add_argument(
        '--install-optional',
        action='store_true',
        help='Install optional dependencies (LlamaIndex)'
    )
    
    args = parser.parse_args()
    # Default behavior: do not auto-pull Ollama models during setup.
    # This avoids downloading local models unless the user explicitly opts in.
    skip_pull = (not args.pull_models) or args.skip_model_pull or args.manual_model

    setup(
        skip_model_pull=skip_pull,
        install_optional=args.install_optional,
    )
