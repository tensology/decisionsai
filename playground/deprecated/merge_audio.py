from pydub import AudioSegment
from pathlib import Path
from tqdm import tqdm
import os

def merge_wav_files():
    # Get the playlist directory path
    playlist_dir = Path('./playlist')
    
    # Get all mp3 files and sort them numerically
    mp3_files = sorted(
        [f for f in playlist_dir.glob('*.mp3')],
        key=lambda x: int(x.stem.split('-')[0])  # Extract number before the hyphen
    )
    
    if not mp3_files:
        print("No MP3 files found in ./playlist directory")
        return
    
    print(f"Found {len(mp3_files)} MP3 files to process")
    
    # Create the final directory if it doesn't exist
    output_dir = Path('./playlist/final')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Start with the first file
    combined = AudioSegment.from_mp3(mp3_files[0])
    
    # Combine all subsequent files with a progress bar
    for mp3_file in tqdm(mp3_files[1:], desc="Merging MP3 files"):
        audio_segment = AudioSegment.from_mp3(mp3_file)
        combined += audio_segment
    
    # Export as MP3 with high quality
    output_path = output_dir / "combined_output.mp3"
    print(f"\nExporting to MP3: {output_path}")
    
    combined.export(
        output_path,
        format="mp3",
        bitrate="320k",  # High quality bitrate
        parameters=["-q:a", "0"]  # Highest quality setting
    )
    
    print("Conversion complete!")

if __name__ == "__main__":
    merge_wav_files() 