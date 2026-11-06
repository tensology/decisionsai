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
# Ornith 9B needs ~16 GB total RAM in practice (model ~6-10 GB resident + app).
# No smaller local chat tier is bundled yet, so machines below the minimum still
# fall back to it (see recommend_model) but with a logged warning.
ORNITH_9B_MIN_RAM_GB = 16
OLLAMA_MODEL_TIERS = [
    (ORNITH_9B_MIN_RAM_GB, "ornith:9b", 6, "local default"),
]

# Vision model tiers
OLLAMA_VISION_TIERS = [
    (0,   "qwen3-vl:235b-cloud",  0, "cloud — no local RAM needed"),
]

# Coding model tiers
OLLAMA_CODING_TIERS = [
    (ORNITH_9B_MIN_RAM_GB, "ornith:9b", 6, "local default"),
]

# Ollama keep_alive: -1 (stay loaded, today's behaviour) unless total RAM is below
# this, where a finite value lets Ollama unload the model between turns.
OLLAMA_KEEP_ALIVE_FINITE_BELOW_RAM_GB = 16
OLLAMA_KEEP_ALIVE_LOW_RAM = "10m"


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

    # Absolute fallback: below every tier's minimum. Keep the smallest tier but warn.
    fallback = tiers[0][1]
    if tiers[0][2]:  # local model (cloud tiers report 0 GB)
        logger.warning(
            "System RAM %.1f GB is below the %s GB minimum for %s; using it anyway "
            "(no smaller local tier available). Expect slow or memory-pressured local inference.",
            ram_gb, tiers[0][0], fallback,
        )
    return fallback


def recommend_ollama_defaults(ram_gb: float = None) -> dict:
    """Return a dict of recommended Ollama models keyed by role.

    >>> recommend_ollama_defaults(16)
    {'conversational': 'ornith:9b', 'coding': 'ornith:9b', 'vision': 'qwen3-vl:235b-cloud'}
    """
    if ram_gb is None:
        ram_gb = get_total_ram_gb()

    return {
        "conversational": recommend_model(ram_gb, OLLAMA_MODEL_TIERS),
        "coding":         recommend_model(ram_gb, OLLAMA_CODING_TIERS),
        "vision":         recommend_model(ram_gb, OLLAMA_VISION_TIERS),
    }


_RAM_CACHE: list = []


def _cached_total_ram_gb() -> float:
    if not _RAM_CACHE:
        _RAM_CACHE.append(get_total_ram_gb())
    return _RAM_CACHE[0]


def ollama_runtime_options(settings: dict = None, ram_gb: float = None, default_num_ctx: int = 8192) -> dict:
    """Return ``{"num_ctx", "keep_alive"}`` for an Ollama call.

    Explicit values (``ollama_num_ctx`` / ``ollama_keep_alive`` keys in ``settings``)
    always win. Otherwise num_ctx is ``default_num_ctx`` (unchanged from today) and
    keep_alive is -1 (stay loaded, today's default) except below 16 GB total RAM,
    where it is "10m" so the model can be unloaded.
    """
    settings = settings or {}
    num_ctx = int(default_num_ctx)
    explicit_ctx = settings.get("ollama_num_ctx")
    try:
        if explicit_ctx not in (None, "") and int(explicit_ctx) > 0:
            num_ctx = int(explicit_ctx)
    except (TypeError, ValueError):
        pass

    explicit_keep = settings.get("ollama_keep_alive")
    if explicit_keep not in (None, ""):
        keep_alive = explicit_keep
        if isinstance(keep_alive, str) and keep_alive.strip().lstrip("-").isdigit():
            keep_alive = int(keep_alive.strip())
    else:
        if ram_gb is None:
            ram_gb = _cached_total_ram_gb()
        keep_alive = OLLAMA_KEEP_ALIVE_LOW_RAM if ram_gb < OLLAMA_KEEP_ALIVE_FINITE_BELOW_RAM_GB else -1
    return {"num_ctx": num_ctx, "keep_alive": keep_alive}


def log_system_resources():
    """Log detected RAM and recommended models (called once at startup)."""
    ram = get_total_ram_gb()
    defaults = recommend_ollama_defaults(ram)
    logger.info(
        "System RAM: %.1f GB → recommended Ollama models: %s",
        ram, ", ".join(f"{k}={v}" for k, v in defaults.items()),
    )
    return ram, defaults
