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


def setup_qwen3_models(model_name: str = None):
    """
    Pre-download Qwen3-TTS models into distr/core/agent/models/ so
    from_pretrained() can load from a local path without HuggingFace cache
    validation on every startup.

    Downloads two models:
      1. CustomVoice — used for preset speakers (normal TTS)
      2. Base — used for voice cloning (custom voices)
    """
    models_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'distr', 'core', 'agent', 'models')
    models_root = os.path.abspath(models_root)

    custom_voice_repo = model_name or "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
    base_repo = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"

    custom_voice_dir = os.path.join(models_root, 'qwen3-tts')
    base_dir = os.path.join(models_root, 'qwen3-tts-base')

    print("")
    print("=" * 60)
    print("Qwen3-TTS Model Setup")
    print("=" * 60)
    print("")

    _download_hf_model(custom_voice_repo, custom_voice_dir, label="CustomVoice model (preset speakers)")
    print("")
    _download_hf_model(base_repo, base_dir, label="Base model (voice cloning)")
    print("")


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


def setup(skip_model_pull=False, install_optional=False, setup_qwen3=False, qwen3_model=None):
    """
    Main setup function to download and extract files.

    Args:
        skip_model_pull: If True, skip automatic model pull and show manual instructions
        install_optional: If True, install optional dependencies (LlamaIndex)
        setup_qwen3: If True, pre-download Qwen3-TTS model from HuggingFace
        qwen3_model: Override the Qwen3 model id (default: 0.6B)
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
        rec = {"conversational": "qwen3:8b", "coding": "qwen2.5-coder:7b", "vision": "qwen3-vl:2b"}

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
        return

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

    # Pre-download Qwen3-TTS model (always — avoids long wait on first voice use)
    setup_qwen3_models(model_name=qwen3_model)

    # Pre-download Kanade voice cloning models (avoids long wait on first custom voice play)
    setup_kanade_models()

    print("All models have been downloaded and set up successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Setup DecisionsAI models and dependencies')
    parser.add_argument(
        '--skip-model-pull',
        action='store_true',
        help='Skip automatic Ollama model pull and show manual instructions instead'
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
    parser.add_argument(
        '--setup-qwen3',
        action='store_true',
        help='Pre-download Qwen3-TTS model from HuggingFace (0.6B by default, ~4GB VRAM)'
    )
    parser.add_argument(
        '--setup-qwen3-only',
        action='store_true',
        help='Only pre-download Qwen3-TTS model (skip Kokoro/Ollama setup)'
    )
    parser.add_argument(
        '--qwen3-model',
        type=str,
        default=None,
        help='Override Qwen3 model id (e.g. Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice for higher quality)'
    )
    
    args = parser.parse_args()
    skip_pull = args.skip_model_pull or args.manual_model

    # --setup-qwen3-only: just download the Qwen3 model and exit
    if args.setup_qwen3_only:
        setup_qwen3_models(model_name=args.qwen3_model)
        sys.exit(0)

    setup(
        skip_model_pull=skip_pull,
        install_optional=args.install_optional,
        setup_qwen3=args.setup_qwen3,
        qwen3_model=args.qwen3_model,
    )
