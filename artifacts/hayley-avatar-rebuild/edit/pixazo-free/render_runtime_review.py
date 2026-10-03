from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PyQt6.QtGui import QGuiApplication, QImage

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = ROOT.parents[1] / "assets" / "avatars" / "hayley"
OUTPUT = ROOT / "runtime-decoder-contact-sheet-v2.png"
MANIFEST = ROOT / "runtime-decoder-contact-sheet-v2.json"
sys.path.insert(0, str(ROOT.parents[1]))

from distr.core.skin_config import EVENT_HOOKS, parse
from distr.gui.oracle.webm_player import WebMPlayer


def qimage_to_pil(image: QImage) -> Image.Image:
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pointer = rgba.bits()
    pointer.setsize(rgba.width() * rgba.height() * 4)
    pixels = np.frombuffer(memoryview(pointer), dtype=np.uint8).reshape(
        (rgba.height(), rgba.width(), 4)
    )
    return Image.fromarray(pixels.copy(), "RGBA")


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication([])
    config = parse((RUNTIME_DIR / "skin.json").read_text())
    cell_width, cell_height = 600, 225
    columns = 3
    rows = (len(EVENT_HOOKS) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (31, 33, 38))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    manifest = []

    for slot, hook in enumerate(EVENT_HOOKS):
        response = config.events[hook]
        player = WebMPlayer()
        player.set_size(180, 180)
        player.load(
            str(RUNTIME_DIR / response.animation),
            playback=response.playback,
            chroma_key=config.rendering.chroma_key,
            chroma_threshold=config.rendering.chroma_threshold,
        )
        if not player._frames:
            raise RuntimeError(f"No decoded frames for {hook}: {response.animation}")
        indices = [0, len(player._frames) // 2, len(player._frames) - 1]
        cell_x = (slot % columns) * cell_width
        cell_y = (slot // columns) * cell_height
        draw.text((cell_x + 12, cell_y + 8), f"{hook}  |  {response.animation}", fill="white", font=font)
        alphas = []
        for frame_slot, index in enumerate(indices):
            rgba = qimage_to_pil(player._frames[index].toImage())
            background = Image.new("RGBA", rgba.size, (92, 96, 105, 255))
            background.alpha_composite(rgba)
            x = cell_x + 12 + frame_slot * 194
            y = cell_y + 38
            sheet.paste(background.convert("RGB"), (x, y))
            alpha = np.asarray(rgba.getchannel("A"))
            alphas.append(
                {
                    "frame": index,
                    "corner_alpha": [
                        int(alpha[0, 0]),
                        int(alpha[0, -1]),
                        int(alpha[-1, 0]),
                        int(alpha[-1, -1]),
                    ],
                }
            )
        manifest.append(
            {
                "hook": hook,
                "animation": response.animation,
                "frames": len(player._frames),
                "samples": alphas,
            }
        )
        player.stop()

    sheet.save(OUTPUT)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(OUTPUT)
    print(MANIFEST)
    del app


if __name__ == "__main__":
    main()
