import requests
import warnings
import zipfile
import logging
import os
import time

# Suppress specific warnings
warnings.filterwarnings("ignore", category=FutureWarning)

# Set logging level to suppress less important messages
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

def download_file(url, filename, progress_callback=None):
    """
    Download a file from the given URL and save it with the specified filename.
    Raises an exception if the download fails.
    """
    msg = f"Starting download from {url} to {filename}"
    print(msg)
    if progress_callback:
        progress_callback(msg)
        
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        # Check if we got HTML instead of a zip file (common error page)
        content_type = response.headers.get('content-type', '').lower()
        if 'text/html' in content_type:
            raise ValueError(f"Server returned HTML instead of a zip file. URL may be incorrect: {url}")
        
        total_size = int(response.headers.get('content-length', 0))
        
        downloaded_size = 0
        last_print_time = time.time()
        
        with open(filename, 'wb') as file:
            for data in response.iter_content(chunk_size=8192):
                size = file.write(data)
                downloaded_size += size
                
                # Print progress update every ~0.1 seconds (faster for GUI)
                current_time = time.time()
                if current_time - last_print_time > 0.1 or downloaded_size == total_size:
                    msg = ""
                    if total_size > 0:
                        percent = (downloaded_size / total_size) * 100
                        downloaded_mb = downloaded_size / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        # Print in a format parsed by the GUI
                        msg = f"Downloading: {percent:.1f}% ({downloaded_mb:.1f}MB/{total_mb:.1f}MB)"
                    else:
                        downloaded_mb = downloaded_size / (1024 * 1024)
                        msg = f"Downloading: {downloaded_mb:.1f}MB"
                    
                    print(msg, flush=True)
                    if progress_callback:
                        progress_callback(msg)
                        
                    last_print_time = current_time
        
        # Verify the downloaded file size matches expected size
        if total_size > 0:
            actual_size = os.path.getsize(filename)
            if actual_size != total_size:
                os.remove(filename)
                raise ValueError(f"Download incomplete. Expected {total_size} bytes, got {actual_size} bytes. Please try again.")
        
        # Verify the downloaded file is not empty
        if os.path.getsize(filename) == 0:
            os.remove(filename)
            raise ValueError("Downloaded file is empty. Download may have failed.")
            
    except Exception as e:
        print(f"Error during download: {str(e)}")
        if progress_callback:
            progress_callback(f"Error during download: {str(e)}")
        raise

def extract_zip(zip_path, extract_to, progress_callback=None):
    """
    Extract a zip file to the specified directory.
    Raises an exception if the file is not a valid zip file.
    """
    # Verify the file exists and is not empty
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"Zip file not found: {zip_path}")
    
    if os.path.getsize(zip_path) == 0:
        raise ValueError(f"Zip file is empty: {zip_path}")
    
    # Check if it's actually a zip file by testing it
    if not zipfile.is_zipfile(zip_path):
        # Try to read the first few bytes to see what we got
        with open(zip_path, 'rb') as f:
            first_bytes = f.read(100)
            if first_bytes.startswith(b'<!DOCTYPE') or first_bytes.startswith(b'<html'):
                raise ValueError(f"Downloaded file appears to be HTML, not a zip file. The download URL may be incorrect or the file may have been corrupted.")
            else:
                raise ValueError(f"File is not a valid zip file. First bytes: {first_bytes[:50]}")
    
    if progress_callback:
        progress_callback("Extracting zip file...")
        
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)

def setup_vosk(progress_callback=None):
    """
    Setup function to download and extract Vosk speech recognition model.
    Vosk is an optional STT (Speech-to-Text) alternative to Whisper.cpp.
    """
    # Create the models directory if it doesn't exist
    os.makedirs('./distr/core/agent/models', exist_ok=True)

    # Define the Vosk model URL and its corresponding local paths
    vosk_model_url = 'https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip'
    vosk_model_filename = './distr/core/agent/models/vosk-model-en-us-0.22.zip'
    vosk_model_folder = './distr/core/agent/models/vosk-model-en-us-0.22'

    print("Setting up Vosk Speech Recognition Model...")
    print("Note: Vosk is an optional STT alternative. Whisper.cpp is the default STT engine.")
    print("")
    
    if progress_callback:
        progress_callback("Setting up Vosk Speech Recognition Model...")

    # Check if Vosk model is already downloaded and extracted
    if not os.path.exists(vosk_model_folder):
        if not os.path.exists(vosk_model_filename):
            # Download the Vosk model
            print(f"Downloading Vosk model (~1.8GB)...")
            print("This may take a while depending on your internet connection.")
            if progress_callback:
                progress_callback("Downloading Vosk model (~1.8GB)...")
                
            try:
                download_file(vosk_model_url, vosk_model_filename, progress_callback)
                print("✓ Download complete.")
                if progress_callback:
                    progress_callback("✓ Download complete.")
            except Exception as e:
                # Clean up partial download
                if os.path.exists(vosk_model_filename):
                    os.remove(vosk_model_filename)
                raise Exception(f"Failed to download Vosk model: {str(e)}")
        
        # Verify the zip file before extracting
        print(f"Verifying downloaded file...")
        if progress_callback:
            progress_callback("Verifying downloaded file...")
            
        if not zipfile.is_zipfile(vosk_model_filename):
            # Clean up invalid file
            os.remove(vosk_model_filename)
            raise ValueError(f"Downloaded file is not a valid zip file. Please try downloading again.")
        
        # Extract the Vosk model
        print(f"Extracting Vosk model...")
        if progress_callback:
            progress_callback("Extracting Vosk model...")
            
        try:
            extract_zip(vosk_model_filename, './distr/core/agent/models', progress_callback)
        except Exception as e:
            # Clean up invalid zip file
            if os.path.exists(vosk_model_filename):
                os.remove(vosk_model_filename)
            raise Exception(f"Failed to extract Vosk model: {str(e)}")

        # Remove the zip file after extraction
        os.remove(vosk_model_filename)
        print("✓ Vosk model setup complete.")
        if progress_callback:
            progress_callback("✓ Vosk model setup complete.")
    else:
        print("✓ Vosk model already exists. Skipping download and extraction.")
        if progress_callback:
            progress_callback("✓ Vosk model already exists.")

    print("")
    print("Vosk model is now available. You can select it in Settings > AI > Transcription Model.")


if __name__ == "__main__":
    setup_vosk()



