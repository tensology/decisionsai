#!/usr/bin/env python3
"""
patch_torch.py — Fix PyTorch loading when Homebrew's system-wide pytorch
libraries conflict with the virtualenv's version.

Problem:
  Homebrew installs pytorch and symlinks libtorch_cpu.dylib,
  libtorch_python.dylib, libtorch.dylib into /opt/homebrew/lib/.
  When the virtualenv's torch loads, macOS dyld finds Homebrew's
  incompatible libraries first (built for a different Python version),
  causing errors like:
    - symbol not found in flat namespace '_PyDict_GetItemRef'
    - Symbol not found: __ZN3c1015SmallVectorBaseIjE13mallocForGrowEmmRm

Fix:
  1. `brew unlink pytorch` — removes the /opt/homebrew/lib symlinks
  2. Reinstall torch in the virtualenv (clean binaries)
  3. Verify the import works

Usage:
  python patch_torch.py                     # auto-detect virtualenv
  python patch_torch.py /path/to/venv       # explicit virtualenv path

Run this after:
  - pip install torch / pip install --upgrade torch
  - pip install <package-that-depends-on-torch>  (e.g. voxcpm)
  - brew upgrade (which may re-link the system pytorch)
"""

import os
import sys
import subprocess
import shutil


# ── Torch version to install (must satisfy voxcpm's >=2.5.0 requirement) ──
TORCH_VERSION = "2.5.1"
TORCHAUDIO_VERSION = "2.5.1"


def find_venv():
    """Find the active virtualenv or the project's default one."""
    if sys.prefix != sys.base_prefix:
        return sys.prefix
    venv = os.environ.get("VIRTUAL_ENV")
    if venv and os.path.isdir(venv):
        return venv
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("venv", ".venv"):
        candidate = os.path.join(script_dir, name)
        if os.path.isdir(candidate):
            return candidate
    home = os.path.expanduser("~")
    for name in ("decisions", "DecisionsAI", "decisionsai"):
        candidate = os.path.join(home, ".virtualenvs", name)
        if os.path.isdir(candidate):
            return candidate
    return None


def get_py_bin(venv_path):
    """Return the python binary inside the virtualenv."""
    for name in ("python", "python3"):
        p = os.path.join(venv_path, "bin", name)
        if os.path.exists(p):
            return p
    return None


def torch_imports_ok(py_bin):
    """Return True if torch imports successfully in the virtualenv."""
    result = subprocess.run(
        [py_bin, "-c", "import torch; print(torch.__version__)"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        return True, result.stdout.strip()
    return False, result.stderr.strip()


def check_homebrew_pytorch():
    """Return list of conflicting Homebrew pytorch symlinks."""
    conflicts = []
    for name in ("libtorch_cpu.dylib", "libtorch_python.dylib", "libtorch.dylib"):
        path = os.path.join("/opt/homebrew/lib", name)
        if os.path.exists(path):
            conflicts.append((name, os.path.realpath(path)))
    return conflicts


def brew_unlink_pytorch():
    """Run `brew unlink pytorch`. Returns True on success."""
    if not shutil.which("brew"):
        return False
    result = subprocess.run(
        ["brew", "unlink", "pytorch"],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


def reinstall_torch(py_bin):
    """Force-reinstall torch + torchaudio with no cache."""
    pip = os.path.join(os.path.dirname(py_bin), "pip")
    if not os.path.exists(pip):
        pip_cmd = [py_bin, "-m", "pip"]
    else:
        pip_cmd = [pip]

    print(f"   Installing torch=={TORCH_VERSION}, torchaudio=={TORCHAUDIO_VERSION}...")
    result = subprocess.run(
        pip_cmd + [
            "install", "--force-reinstall", "--no-cache-dir", "--no-deps",
            f"torch=={TORCH_VERSION}", f"torchaudio=={TORCHAUDIO_VERSION}",
        ],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        print(f"   ⚠️  pip install failed: {result.stderr.strip()[-200:]}")
        return False
    return True


def main():
    venv_path = sys.argv[1] if len(sys.argv) > 1 else find_venv()
    if not venv_path or not os.path.isdir(venv_path):
        print("ERROR: Could not find virtualenv. Pass the path as an argument:")
        print(f"  python {sys.argv[0]} /path/to/venv")
        sys.exit(1)

    py_bin = get_py_bin(venv_path)
    if not py_bin:
        print(f"ERROR: No python binary found in {venv_path}/bin/")
        sys.exit(1)

    print(f"Virtualenv: {venv_path}")
    print(f"Python:     {py_bin}")
    print()

    # ── Step 0: Quick check — maybe torch already works ──
    ok, ver = torch_imports_ok(py_bin)
    if ok:
        print(f"✅ torch {ver} already works. Nothing to do.")
        sys.exit(0)

    print("❌ torch import failed. Diagnosing...")
    print()

    # ── Step 1: Check for Homebrew pytorch conflict ──
    conflicts = check_homebrew_pytorch()
    if conflicts:
        print("⚠️  Homebrew pytorch found in /opt/homebrew/lib/:")
        for name, real_path in conflicts:
            print(f"   {name} -> {real_path}")
        print()
        print("Step 1: Unlinking Homebrew pytorch...")
        if brew_unlink_pytorch():
            print("   ✅ brew unlink pytorch — done")
        else:
            print("   ⚠️  brew unlink failed. Try manually: brew unlink pytorch")
        print()

        # Verify after unlink
        remaining = check_homebrew_pytorch()
        if remaining:
            print("   Symlinks still present — trying brew uninstall...")
            subprocess.run(
                ["brew", "uninstall", "--ignore-dependencies", "pytorch"],
                capture_output=True, text=True, timeout=30,
            )
    else:
        print("   No Homebrew pytorch conflict detected.")
        print()

    # ── Step 2: Reinstall torch (clean binaries) ──
    print("Step 2: Reinstalling torch (clean binaries)...")
    if reinstall_torch(py_bin):
        print("   ✅ torch reinstalled")
    else:
        print("   ❌ reinstall failed")
    print()

    # ── Step 3: Verify ──
    print("Step 3: Verifying...")
    ok, ver = torch_imports_ok(py_bin)
    if ok:
        print(f"✅ torch {ver} loaded successfully!")
        print()
        print("Done. Run this script again after any `brew upgrade` or torch reinstall.")
        sys.exit(0)
    else:
        # Print the key error line
        for line in ver.splitlines():
            if "Error" in line:
                print(f"   {line.strip()}")
        print()
        print("❌ torch still broken. Possible fixes:")
        print("   1. brew uninstall pytorch")
        print("   2. Recreate the virtualenv from scratch")
        print(f"   3. Check: {py_bin} -c 'import torch'")
        sys.exit(1)


if __name__ == "__main__":
    main()
