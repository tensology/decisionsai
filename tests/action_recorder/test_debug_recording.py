#!/usr/bin/env python3

"""
Debug test to see what files are in the recordings directory.
"""

import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from distr.core.paths import RECORDINGS_DIR

def debug_recordings():
    """Debug what files are in the recordings directory"""
    print("=" * 80)
    print("DEBUG RECORDINGS DIRECTORY")
    print("=" * 80)

    recordings_path = Path(RECORDINGS_DIR)
    print(f"Recordings directory: {recordings_path}")

    if recordings_path.exists():
        print("Files in recordings directory:")
        for file in recordings_path.iterdir():
            if file.is_file():
                print(f"  - {file.name}")
    else:
        print("Recordings directory does not exist")

    print("=" * 80)

if __name__ == '__main__':
    debug_recordings()