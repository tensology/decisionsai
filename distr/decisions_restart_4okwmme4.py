#!/usr/bin/env python3
import subprocess
import sys
import time
import os

# Wait a moment for the old process to fully exit
time.sleep(2)

# Launch the application
command = ['/Users/paul/.virtualenvs/decisions/bin/python', 'bin/start.py']
base_dir = '/Users/paul/development/TENSOLOGY/DecisionsAI/distr'

try:
    import platform
    if sys.platform == 'darwin' and command[0] == 'open':
        subprocess.Popen(command, cwd=base_dir)
    elif platform.system() == 'Windows':
        creation_flags = 0
        if hasattr(subprocess, 'DETACHED_PROCESS'):
            creation_flags = subprocess.DETACHED_PROCESS
        elif hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP'):
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(command, cwd=base_dir, creationflags=creation_flags)
    else:
        subprocess.Popen(command, cwd=base_dir, start_new_session=True)
    print(f"Restarted application: {' '.join(command)}")
except Exception as e:
    print(f"Error restarting application: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    try:
        script_path = os.path.abspath(__file__)
        if os.path.exists(script_path):
            os.unlink(script_path)
    except OSError:
        pass
