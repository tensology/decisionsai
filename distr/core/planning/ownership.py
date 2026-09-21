"""Conservative local process ownership, never a time-based planning lease."""
import os
import socket

import psutil


def current_owner():
    return {"host": socket.gethostname(), "pid": os.getpid(),
            "started": psutil.Process(os.getpid()).create_time()}


def owner_is_gone(owner):
    """Unknown/remote/permission-denied owners stay locked. PID reuse is safe."""
    if not isinstance(owner, dict) or owner.get("host") != socket.gethostname():
        return False
    pid, started = owner.get("pid"), owner.get("started")
    if type(pid) is not int or pid <= 0 or type(started) not in (int, float):
        return False
    try:
        process = psutil.Process(pid)
        return process.create_time() != started or process.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, OSError):
        return False
