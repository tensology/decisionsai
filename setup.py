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

def setup():
    """
    Main setup function to download and extract files.
    """
    # Create the models directory if it doesn't exist
    os.makedirs('./distr/agent/models', exist_ok=True)

    # Define kokoro model files and URLs
    kokoro_files = {
        'model': {
            'url': 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx',
            'filename': './distr/agent/models/kokoro-v1.0.onnx'
        },
        'voices': {
            'url': 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin',
            'filename': './distr/agent/models/voices-v1.0.bin'
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

    # Define the Vosk model URL and its corresponding local paths
    vosk_model_url = 'https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip'
    vosk_model_filename = './distr/agent/models/vosk-model-en-us-0.22.zip'
    vosk_model_folder = './distr/agent/models/vosk-model-en-us-0.22'

    # Check if Vosk model is already downloaded and extracted
    if not os.path.exists(vosk_model_folder):
        if not os.path.exists(vosk_model_filename):
            # Download the Vosk model
            print(f"Downloading Vosk model...")
            download_file(vosk_model_url, vosk_model_filename)
        
        # Extract the Vosk model
        print(f"Extracting Vosk model...")
        extract_zip(vosk_model_filename, './distr/agent/models')

        # Optionally, remove the zip file after extraction
        os.remove(vosk_model_filename)
        print("Vosk model setup complete.")
    else:
        print("Vosk model already exists. Skipping download and extraction.")

    print("Setting up Ollama models...")
    model_name = "gemma3:4b"

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

    if not check_model_status(model_name):
        print(f"Pulling model {model_name}...")
        ollama.pull(model_name)
    else:
        print(f"Model {model_name} is up to date.")

    os.makedirs("assets/tmp", exist_ok=True)

    # Install spaCy model
    print("Setting up spaCy model for sentence splitting...")
    
    # First check if spaCy is installed, install if not
    try:
        import spacy
        print("spaCy is already installed.")
    except ImportError:
        print("Installing spaCy...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'spacy'])
    
    # Install large spaCy model
    print("Installing large spaCy language model...")
    
    # Check if model is already installed
    try:
        import spacy
        spacy.load("en_core_web_lg")
        print("Large spaCy model already installed.")
    except (OSError, ImportError):
        print("Downloading large spaCy model (this may take a while)...")
        subprocess.check_call([sys.executable, '-m', 'spacy', 'download', 'en_core_web_lg'])
    
    print("All models have been downloaded and set up successfully.")


if __name__ == "__main__":
    setup()
