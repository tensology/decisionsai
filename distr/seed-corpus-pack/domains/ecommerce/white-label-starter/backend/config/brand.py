"""Brand tokens shared by Unfold admin and documented for frontend CSS vars."""
from __future__ import annotations
import os

# Placeholder sky/indigo — BrandPack later overlays these.
DEFAULT_PRIMARY = {
    "50": "#f0f9ff",
    "100": "#e0f2fe",
    "200": "#bae6fd",
    "300": "#7dd3fc",
    "400": "#38bdf8",
    "500": os.environ.get("BRAND_PRIMARY_500", "#0ea5e9"),
    "600": os.environ.get("BRAND_PRIMARY_600", "#0284c7"),
    "700": "#0369a1",
    "800": "#075985",
    "900": "#0c4a6e",
    "950": "#082f49",
}

def apply_brand_to_unfold(brand: dict | None = None) -> dict:
    """Map BrandPack primary shades into UNFOLD["COLORS"]["primary"]."""
    primary = (brand or {}).get("primary") or DEFAULT_PRIMARY
    return {
        "primary": {str(k): v for k, v in primary.items()},
    }

def css_variables(brand: dict | None = None) -> str:
    primary = (brand or {}).get("primary") or DEFAULT_PRIMARY
    lines = [f"  --color-primary-{k}: {v};" for k, v in primary.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"
