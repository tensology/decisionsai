from contextlib import contextmanager
import sys
import io
import os
import warnings
import time
import datetime

# Suppress specific warnings
warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', category=FutureWarning)
warnings.filterwarnings('ignore', category=DeprecationWarning)

@contextmanager
def suppress_stdout():
    """Context manager to temporarily suppress stdout and stderr"""
    # Save the current stdout and stderr
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    # Redirect stdout and stderr to devnull
    with open(os.devnull, 'w') as devnull:
        sys.stdout = devnull
        sys.stderr = devnull
        try:
            yield
        finally:
            # Restore stdout and stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr


@contextmanager
def suppress_vosk_logs():
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        os.dup2(old_stderr, 2)
        os.close(devnull)

def get_timestamp():
    """Return a formatted timestamp for logging"""
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
