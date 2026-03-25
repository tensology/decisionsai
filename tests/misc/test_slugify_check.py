#!/usr/bin/env python3

"""
Check which slugify function is being used.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from distr.core.action_recorder import slugify as slugify_main
from distr.core.action_recorder_process import slugify as slugify_process

def test_slugify():
    """Test both slugify functions"""
    action_title = "Test Playback Action"

    main_result = slugify_main(action_title)
    process_result = slugify_process(action_title)

    print(f"Action title: '{action_title}'")
    print(f"Main slugify result: '{main_result}'")
    print(f"Process slugify result: '{process_result}'")
    print(f"Results match: {main_result == process_result}")

if __name__ == '__main__':
    test_slugify()
