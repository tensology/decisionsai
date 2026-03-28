import os
import sys
import warnings
warnings.filterwarnings("ignore", message=".*DecompressionBomb.*")

# Fix Windows console encoding — cp1252 can't handle emoji in log messages
# Only do this in the main process, not in multiprocessing spawn workers
if sys.platform == 'win32' and __name__ == '__main__':
    import io
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

# On Windows, explicitly add onnxruntime's DLL directory to the search path
# and import it FIRST, before PyTorch or other native libraries can pollute
# the DLL search order and cause onnxruntime_pybind11_state to fail.
if sys.platform == 'win32':
    try:
        import importlib.util
        _ort_spec = importlib.util.find_spec('onnxruntime')
        if _ort_spec and _ort_spec.submodule_search_locations:
            _ort_dir = _ort_spec.submodule_search_locations[0]
            _capi_dir = os.path.join(_ort_dir, 'capi')
            # Add both the package dir and capi dir so Windows finds the native DLLs
            if os.path.isdir(_capi_dir):
                os.add_dll_directory(_capi_dir)
            if os.path.isdir(_ort_dir):
                os.add_dll_directory(_ort_dir)
        import onnxruntime  # noqa: F401 — must load before torch
    except (ImportError, OSError):
        pass

# Fix for macOS: DYLD_LIBRARY_PATH is searched BEFORE @rpath by the dynamic
# linker.  If Homebrew's /opt/homebrew/lib contains libc10.dylib (symlinked
# from its own PyTorch for Python 3.14), torch's @rpath-based libc10 is
# shadowed and we get _PyDict_GetItemRef symbol errors.
#
# IMPORTANT: dyld reads DYLD_* vars at process start and caches them.
# Changing os.environ after launch has NO effect on dlopen().  We must
# detect the conflict and re-exec ourselves with the fixed environment.
if sys.platform == 'darwin' and not os.environ.get('_DECISIONS_DYLD_FIXED'):
    def _has_libc10(directory):
        """Check if a directory contains libc10.dylib (PyTorch conflict)."""
        try:
            return os.path.exists(os.path.join(directory, 'libc10.dylib'))
        except OSError:
            return False

    _dyld_val = os.environ.get('DYLD_LIBRARY_PATH', '')
    _needs_reexec = False
    if _dyld_val:
        _safe = []
        _conflicting = []
        for _p in _dyld_val.split(':'):
            if _p:
                (_conflicting if _has_libc10(_p) else _safe).append(_p)
        if _conflicting:
            _needs_reexec = True
            # Keep only non-conflicting paths in DYLD_LIBRARY_PATH
            if _safe:
                os.environ['DYLD_LIBRARY_PATH'] = ':'.join(_safe)
            else:
                os.environ.pop('DYLD_LIBRARY_PATH', None)
            # Demote conflicting paths to DYLD_FALLBACK_LIBRARY_PATH (searched after @rpath)
            _fb = os.environ.get('DYLD_FALLBACK_LIBRARY_PATH', '')
            _fb_parts = [_p for _p in _fb.split(':') if _p] if _fb else []
            for _cp in _conflicting:
                if _cp not in _fb_parts:
                    _fb_parts.append(_cp)
            os.environ['DYLD_FALLBACK_LIBRARY_PATH'] = ':'.join(_fb_parts)

    if _needs_reexec:
        # Mark that we've fixed the env so we don't loop
        os.environ['_DECISIONS_DYLD_FIXED'] = '1'
        # Re-exec with the same Python and args so dyld picks up the new env
        os.execv(sys.executable, [sys.executable] + sys.argv)

# Suppress huggingface_hub "Fetching N files" tqdm progress bars (cache validation noise)
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')

# Fix for Qt WebEngine rendering on macOS - must be set BEFORE any Qt imports
if sys.platform == 'darwin':
    os.environ.setdefault('QT_MAC_WANTS_LAYER', '1')

import multiprocessing

# freeze_support() MUST be called at module level before any other code on Windows
# to handle the multiprocessing spawn bootstrap correctly.
multiprocessing.freeze_support()

# Fix for macOS multiprocessing spawn issues with semaphores
# Must be done BEFORE any other multiprocessing usage
if sys.platform == 'darwin':
    try:
        multiprocessing.set_start_method('spawn', force=False)
    except RuntimeError:
        pass  # Already set

# Add project root to Python path (one directory up from bin/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = os.path.join(PROJECT_ROOT, '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
except ImportError:
    # python-dotenv not installed, try simple manual parsing
    env_path = os.path.join(PROJECT_ROOT, '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())

# Suppress MallocStackLogging warnings on macOS - MUST be set before any other imports
# This prevents macOS from logging memory allocation tracking messages
if sys.platform == 'darwin':
    # Completely remove the variable rather than setting to "0" to prevent warnings
    if "MallocStackLogging" in os.environ:
        del os.environ["MallocStackLogging"]
    if "MallocStackLoggingDirectory" in os.environ:
        del os.environ["MallocStackLoggingDirectory"]


    # Import AppKit only on macOS
    try:
        import AppKit
        AppKit.NSBundle.mainBundle().infoDictionary()['LSUIElement'] = '1'
    except ImportError:
        print("Warning: AppKit (PyObjC) not found. Install with: pip install pyobjc")
        print("Continuing anyway...")

def kill_existing_decisions_processes():
    """Check for and kill any existing Decisions processes before starting"""
    import subprocess
    import platform
    
    system = platform.system()
    killed_count = 0
    
    try:
        if system == 'Darwin':  # macOS
            # Find processes running bin/start.py or distr/app.py
            try:
                # Find PIDs of processes containing our script paths
                ps_output = subprocess.check_output(
                    ['ps', 'aux'], 
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                
                # Look for processes that match our application
                lines = ps_output.split('\n')
                pids_to_kill = []
                
                for line in lines:
                    # Skip header line
                    if 'PID' in line and 'COMMAND' in line:
                        continue
                    
                    # Check if this line contains our script paths
                    if 'bin/start.py' in line or 'distr/app.py' in line:
                        # Extract PID (second column in ps aux)
                        parts = line.split()
                        if len(parts) > 1:
                            try:
                                pid = int(parts[1])
                                # Don't kill ourselves
                                if pid != os.getpid():
                                    pids_to_kill.append(pid)
                            except (ValueError, IndexError):
                                continue
                
                # Kill the processes
                for pid in pids_to_kill:
                    try:
                        # Try graceful termination first
                        os.kill(pid, 15)  # SIGTERM
                        killed_count += 1
                        print(f"Terminated existing Decisions process (PID: {pid})")
                    except ProcessLookupError:
                        # Process already gone
                        pass
                    except PermissionError:
                        # Try with kill command
                        try:
                            subprocess.run(['kill', str(pid)], check=False, 
                                         stderr=subprocess.DEVNULL, 
                                         stdout=subprocess.DEVNULL)
                            killed_count += 1
                            print(f"Terminated existing Decisions process (PID: {pid})")
                        except:
                            pass
                
                # Wait a moment for processes to terminate
                if killed_count > 0:
                    import time
                    time.sleep(1)
                    
                    # Force kill any that are still running
                    for pid in pids_to_kill:
                        try:
                            # Check if process still exists
                            os.kill(pid, 0)  # Signal 0 just checks if process exists
                            # Still running, force kill
                            os.kill(pid, 9)  # SIGKILL
                            print(f"Force killed Decisions process (PID: {pid})")
                        except ProcessLookupError:
                            # Already gone, good
                            pass
                        except PermissionError:
                            try:
                                subprocess.run(['kill', '-9', str(pid)], check=False,
                                             stderr=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL)
                            except:
                                pass
                        except:
                            pass
                            
            except subprocess.CalledProcessError:
                pass
            except Exception as e:
                # Silently fail - don't block startup if we can't check
                pass
                
        elif system == 'Windows':
            # On Windows, skip killing other python processes — too risky as it
            # can kill the multiprocessing Manager server or other unrelated processes.
            pass
                
        else:  # Linux/Unix
            # Similar to macOS but use pgrep if available
            try:
                # Try pgrep first (more reliable)
                try:
                    pgrep_output = subprocess.check_output(
                        ['pgrep', '-f', 'bin/start.py'],
                        stderr=subprocess.DEVNULL,
                        text=True
                    )
                    pids = [int(pid.strip()) for pid in pgrep_output.strip().split('\n') if pid.strip()]
                    
                    # Also check for distr/app.py
                    try:
                        pgrep_output2 = subprocess.check_output(
                            ['pgrep', '-f', 'distr/app.py'],
                            stderr=subprocess.DEVNULL,
                            text=True
                        )
                        pids.extend([int(pid.strip()) for pid in pgrep_output2.strip().split('\n') if pid.strip()])
                    except:
                        pass
                    
                    # Remove duplicates and current PID
                    pids = list(set([p for p in pids if p != os.getpid()]))
                    
                    for pid in pids:
                        try:
                            os.kill(pid, 15)  # SIGTERM
                            killed_count += 1
                            print(f"Terminated existing Decisions process (PID: {pid})")
                        except ProcessLookupError:
                            pass
                        except PermissionError:
                            try:
                                subprocess.run(['kill', str(pid)], check=False,
                                             stderr=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL)
                                killed_count += 1
                            except:
                                pass
                    
                    if killed_count > 0:
                        import time
                        time.sleep(1)
                        
                        # Force kill any remaining
                        for pid in pids:
                            try:
                                os.kill(pid, 0)  # Check if exists
                                os.kill(pid, 9)  # SIGKILL
                            except:
                                pass
                                
                except FileNotFoundError:
                    # pgrep not available, use ps like macOS
                    ps_output = subprocess.check_output(
                        ['ps', 'aux'],
                        stderr=subprocess.DEVNULL,
                        text=True
                    )
                    
                    lines = ps_output.split('\n')
                    pids_to_kill = []
                    
                    for line in lines:
                        if 'bin/start.py' in line or 'distr/app.py' in line:
                            parts = line.split()
                            if len(parts) > 1:
                                try:
                                    pid = int(parts[1])
                                    if pid != os.getpid():
                                        pids_to_kill.append(pid)
                                except (ValueError, IndexError):
                                    continue
                    
                    for pid in pids_to_kill:
                        try:
                            os.kill(pid, 15)
                            killed_count += 1
                            print(f"Terminated existing Decisions process (PID: {pid})")
                        except:
                            pass
                    
                    if killed_count > 0:
                        import time
                        time.sleep(1)
                        
            except Exception:
                pass
    
    except Exception:
        # Silently fail - don't block startup
        pass
    
    if killed_count > 0:
        print(f"Killed {killed_count} existing Decisions process(es)")

# Main execution block
if __name__ == "__main__":
    from distr.app.main import run
    kill_existing_decisions_processes()
    print("Starting Decisions...")
    run()
