# Bug Report: decisions.bat Fails on Install

**Date:** 2026-03-27  
**Environment:** Windows 10/11, Python 3.12.10, pip 26.0.1  
**Symptom:** Running `decisions.bat` appears to start, prints setup progress, then silently exits with a non-zero error code.

---

## Root Cause

`bin/decisions.bat` calculates the project root directory into `SCRIPT_DIR` but **never changes the working directory to it**. All `pip install -r requirements.txt` calls use a bare relative path, so they resolve against whatever directory the script was launched from.

When `decisions.bat` (the root wrapper) calls `bin\decisions.bat`, the working directory is the project root — so it works. But when `bin\decisions.bat` is invoked directly, or when the `installer\.requirements_installed_external` marker is absent (triggering a reinstall), the pip call fails immediately with:

```
ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
```

The script then falls through three retry paths (VS dev env, skip pywhispercpp, cache purge) — all of which reference the same missing relative path — before finally hitting:

```
Error: pip install failed. Please check the output above.
exit /b 1
```

At that point the script exits. Because the terminal window was opened by the bat file itself, it closes immediately, making it look like the app "just dies."

---

## Affected Lines

`bin/decisions.bat`, lines ~316–360. All occurrences of:

```bat
pip.exe install --no-cache-dir -r requirements.txt
pip.exe install --no-cache-dir -r requirements_win.txt
python.exe -c "lines=open('requirements.txt')..."
```

---

## Fix

Add a `cd` to the project root immediately after `SCRIPT_DIR` is resolved (around line 11), before any file operations:

```bat
:: Get the project root directory (parent of bin\)
set "SCRIPT_DIR=%~dp0.."
pushd "%SCRIPT_DIR%"
set "SCRIPT_DIR=%CD%"
:: Stay in the project root for the rest of the script
:: (do NOT popd here — all relative paths depend on this)
```

Or alternatively, prefix every file reference with `%SCRIPT_DIR%\`:

```bat
pip.exe install --no-cache-dir -r "%SCRIPT_DIR%\requirements.txt"
```

---

## Secondary Issues Observed

1. **Long path support warning** — The script attempts to write to `HKLM` registry without elevation. It fails silently with a warning but continues. Not a blocker, but the user should run as Administrator or enable long paths manually.

2. **`installer\.requirements_installed_external` marker logic** — The marker check calls `check_deps.py` to validate installed packages. If the marker is missing (e.g. fresh clone, or manually deleted), the full reinstall is triggered — which hits the `requirements.txt` path bug above. This means the bug surfaces on every fresh install.

3. **Silent exit** — When the script calls `exit /b 1`, the terminal window that launched it closes immediately with no pause, giving the user no time to read the error. Adding `pause` before fatal `exit /b 1` calls would make failures visible.
