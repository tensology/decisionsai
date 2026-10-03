from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "final-animations" / "idle.webm"
EXTENSION_PATH = ROOT / "final-animations" / "idle-extension.webm"
OUTPUT_PATH = ROOT / "final-animations" / "idle-extended.webm"
VERIFY_DIR = Path(__file__).resolve().parent / "verify" / "idle-extended"
FPS = 24
SIZE = 960


def decode(path: Path) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(path))
    frames: list[np.ndarray] = []
    while True:
        ok, bgr = capture.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    capture.release()
    if not frames:
        raise RuntimeError(f"No frames decoded from {path}")
    return frames


def encode(frames: list[np.ndarray]) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{SIZE}x{SIZE}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libvpx-vp9",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "28",
        "-b:v",
        "0",
        str(OUTPUT_PATH),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    result = process.wait()
    if result:
        raise RuntimeError(f"ffmpeg failed with exit code {result}")


def keyed_composite(frame: np.ndarray) -> Image.Image:
    rgb = frame.astype(np.int16)
    green = rgb[:, :, 1]
    red_blue = np.maximum(rgb[:, :, 0], rgb[:, :, 2])
    key_strength = np.clip((green - red_blue - 18) * 4, 0, 255).astype(np.uint8)
    alpha = 255 - key_strength
    alpha = cv2.GaussianBlur(alpha, (0, 0), 0.75)
    rgba = Image.fromarray(np.dstack((frame, alpha)).astype(np.uint8))
    background = Image.new("RGBA", rgba.size, (92, 96, 105, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def render_review(frames: list[np.ndarray], join_index: int) -> Path:
    VERIFY_DIR.mkdir(parents=True, exist_ok=True)
    columns = 14
    thumb_size = 96
    label_height = 18
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (thumb_size * columns, (thumb_size + label_height) * rows),
        (35, 37, 42),
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=12)
    for index, frame in enumerate(frames):
        image = keyed_composite(frame).resize((thumb_size, thumb_size), Image.Resampling.LANCZOS)
        column = index % columns
        row = index // columns
        x = column * thumb_size
        y = row * (thumb_size + label_height)
        sheet.paste(image, (x, y))
        label = f"f{index:03d}"
        fill = (255, 196, 64) if index in {join_index - 1, join_index} else (235, 235, 235)
        draw.text((x + 4, y + thumb_size + 3), label, fill=fill, font=font)
    output = VERIFY_DIR / "every-frame-contact-sheet.png"
    sheet.save(output)
    return output


def mean_abs_diff(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.abs(left.astype(np.int16) - right.astype(np.int16)).mean())


def main() -> None:
    base = decode(BASE_PATH)
    extension = decode(EXTENSION_PATH)
    join_index = len(base)
    frames = base + extension[1:]
    encode(frames)
    review = render_review(frames, join_index)
    manifest = {
        "base": str(BASE_PATH),
        "extension": str(EXTENSION_PATH),
        "output": str(OUTPUT_PATH),
        "review": str(review),
        "base_frames": len(base),
        "extension_frames": len(extension),
        "output_frames": len(frames),
        "fps": FPS,
        "duration_seconds": len(frames) / FPS,
        "join_index": join_index,
        "join_mean_abs_diff": mean_abs_diff(frames[join_index - 1], frames[join_index]),
        "loop_mean_abs_diff": mean_abs_diff(frames[-1], frames[0]),
    }
    (VERIFY_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
