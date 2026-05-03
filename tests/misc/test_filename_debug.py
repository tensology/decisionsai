#!/usr/bin/env python3

"""
Debug test to see what filename should be generated for a specific action title.
"""

import sys
import os

import pytest

pytest.importorskip("pynput")

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from distr.core.actions.recorder import slugify

def debug_filename():
    """Debug what filename should be generated"""
    action_title = "Test Playback Action"
    slug = slugify(action_title)
    filename = f"{slug}.json"
    print(f"Action title: '{action_title}'")
    print(f"Generated slug: '{slug}'")
    print(f"Expected filename: '{filename}'")

if __name__ == '__main__':
    debug_filename()