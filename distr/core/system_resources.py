"""System resource detection for adaptive model selection.

Detects available RAM and recommends the current default Ollama models.
"""

import logging
import platform
import subprocess

logger = logging.getLogger(__name__)

# Model tiers ordered by RAM requirement (ascending).
# Each entry: (min_ram_gb, model_name, approx_vram_gb, label)
# NOTE: min_ram_gb accounts for ~5 GB app overhead (PyQt, pipecat, whisper, torch).
OLLAMA_MODEL_TIERS = [
    (0,   "ornith:9b", 6, "local default"),
]

# Vision model tiers
OLLAMA_VISION_TIERS = [
    (0,   "qwen3-vl:235b-cloud",  0, "cloud — no local RAM needed"),
]

# Coding model tiers
OLLAMA_CODING_TIERS = [
    (0,   "ornith:9b", 6, "local default"),
]


def get_total_ram_gb() -> float:
    """Return total physical RAM in GB. Falls back to 16 if detection fails."""
    try:
        system = platform.system()
        if system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out) / (1024 ** 3)
        elif system == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb / (1024 ** 2)
        elif system == "Windows":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
    except Exception as e:
        logger.warning("Could not detect system RAM: %s — assuming 16 GB", e)
    return 16.0


def recommend_model(ram_gb: float = None, tiers: list = None) -> str:
    """Pick the best model for the available RAM.

    Walks the tier list in reverse (largest first) and returns the first
    model whose min_ram_gb ≤ ram_gb.
    """
    if ram_gb is None:
        ram_gb = get_total_ram_gb()
    if tiers is None:
        tiers = OLLAMA_MODEL_TIERS

    # Walk from largest to smallest, pick the biggest that fits
    for min_ram, model, _, _ in reversed(tiers):
        if ram_gb >= min_ram:
            return model

    # Absolute fallback
    return tiers[0][1]


def recommend_ollama_defaults(ram_gb: float = None) -> dict:
    """Return a dict of recommended Ollama models keyed by role.

    >>> recommend_ollama_defaults(8)
    {'conversational': 'ornith:9b', 'coding': 'ornith:9b', 'vision': 'qwen3-vl:235b-cloud'}
    """
    if ram_gb is None:
        ram_gb = get_total_ram_gb()

    return {
        "conversational": recommend_model(ram_gb, OLLAMA_MODEL_TIERS),
        "coding":         recommend_model(ram_gb, OLLAMA_CODING_TIERS),
        "vision":         recommend_model(ram_gb, OLLAMA_VISION_TIERS),
    }


def log_system_resources():
    """Log detected RAM and recommended models (called once at startup)."""
    ram = get_total_ram_gb()
    defaults = recommend_ollama_defaults(ram)
    logger.info(
        "System RAM: %.1f GB → recommended Ollama models: %s",
        ram, ", ".join(f"{k}={v}" for k, v in defaults.items()),
    )
    return ram, defaults
