from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "final-images-v2"
RAW_DIR = Path(__file__).resolve().parent / "raw"
FINAL_DIR = ROOT / "final-animations"
VERIFY_DIR = Path(__file__).resolve().parent / "verify"
SIZE = 960
FPS = 24
PROFILES: dict[str, tuple[int, int]] = {
    "dictation": (19, 2),
    "file-drop-success": (13, 2),
    "hands-free-listening": (19, 2),
    "idle-extension": (61, 1),
    "ptt-active": (19, 2),
    "running-action": (31, 1),
    "running-step-runner": (25, 1),
    "ticket-dictation": (13, 2),
    "tts-response": (13, 2),
    "talking": (25, 1),
    "needs-attention": (19, 2),
}
FRAME_SCALES: dict[str, float] = {
    "recording-action": 0.88,
}
START_FRAMES: dict[str, int] = {
    "talking": 0,
}
PRESERVE_SOURCE_CORE: dict[str, bool] = {
    "talking": True,
}
SOURCE_STEMS = {
    "idle-extension": "idle",
    "talking": "idle",
}


def crop_square(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    side = min(height, width)
    x = (width - side) // 2
    y = (height - side) // 2
    return frame[y : y + side, x : x + side]


def reconstruct_alpha(
    rgb: np.ndarray,
    source_alpha: np.ndarray,
    preserve_source_core: bool = True,
) -> np.ndarray:
    score = rgb.max(axis=2)
    support = cv2.dilate((source_alpha > 0).astype(np.uint8), np.ones((25, 25), np.uint8)) > 0
    # Generated frames are composited over black. Preserve dark interior art
    # from the approved source mask, while admitting shifted non-black edge
    # pixels only near that mask. This avoids enclosed black-background wedges
    # between props and hair that a border flood-fill would misclassify.
    foreground = (score >= 8) & support
    if preserve_source_core:
        foreground |= source_alpha > 24
    alpha = cv2.GaussianBlur((foreground.astype(np.uint8) * 255), (0, 0), 0.65)
    if preserve_source_core:
        core = cv2.erode((source_alpha > 32).astype(np.uint8), np.ones((17, 17), np.uint8)) * 255
        alpha = np.maximum(alpha, core)
    return alpha.astype(np.uint8)


def fit_with_margin(image: np.ndarray, scale: float) -> np.ndarray:
    if scale >= 0.999:
        return image
    resized_size = max(1, round(SIZE * scale))
    interpolation = cv2.INTER_LANCZOS4 if image.ndim == 3 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (resized_size, resized_size), interpolation=interpolation)
    output_shape = (SIZE, SIZE, image.shape[2]) if image.ndim == 3 else (SIZE, SIZE)
    output = np.zeros(output_shape, dtype=image.dtype)
    x = (SIZE - resized_size) // 2
    y = max(20, (SIZE - resized_size) // 3)
    output[y : y + resized_size, x : x + resized_size] = resized
    return output


def read_loop_frames(
    video_path: Path,
    start_frame: int,
    forward_count: int,
    repeat: int,
    frame_scale: float,
) -> list[np.ndarray]:
    capture = cv2.VideoCapture(str(video_path))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frames: list[np.ndarray] = []
    while len(frames) < forward_count:
        ok, bgr = capture.read()
        if not ok:
            break
        square = crop_square(bgr)
        resized = cv2.resize(square, (SIZE, SIZE), interpolation=cv2.INTER_LANCZOS4)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        frames.append(fit_with_margin(rgb, frame_scale))
    capture.release()
    if len(frames) < forward_count:
        raise RuntimeError(
            f"{video_path.name}: expected at least {forward_count} frames, found {len(frames)}"
        )
    loop = frames + list(reversed(frames[:-1]))
    return [frame for frame in loop for _ in range(repeat)]


def encode_loop(
    stem: str,
    frames: list[np.ndarray],
    source_alpha: np.ndarray,
    preserve_source_core: bool,
) -> Path:
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    output = FINAL_DIR / f"{stem}.webm"
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
        str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    for rgb in frames:
        alpha = reconstruct_alpha(rgb, source_alpha, preserve_source_core)
        weight = (alpha.astype(np.float32) / 255.0)[:, :, None]
        key = np.zeros_like(rgb, dtype=np.float32)
        key[:, :, 1] = 255
        keyed_rgb = (rgb.astype(np.float32) * weight + key * (1.0 - weight)).astype(np.uint8)
        process.stdin.write(keyed_rgb.tobytes())
    process.stdin.close()
    result = process.wait()
    if result:
        raise RuntimeError(f"ffmpeg failed for {stem} with exit code {result}")
    return output


def composite(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    rgba = Image.fromarray(np.dstack((rgb, alpha)).astype(np.uint8))
    background = Image.new("RGBA", rgba.size, (92, 96, 105, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def render_review(
    stem: str,
    frames: list[np.ndarray],
    source_alpha: np.ndarray,
    preserve_source_core: bool,
) -> Path:
    review_dir = VERIFY_DIR / stem
    review_dir.mkdir(parents=True, exist_ok=True)
    indices = list(range(len(frames)))
    columns = 7
    rows = (len(indices) + columns - 1) // columns
    thumb_size = 170
    label_height = 24
    sheet = Image.new("RGB", (thumb_size * columns, (thumb_size + label_height) * rows), (35, 37, 42))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    for slot, index in enumerate(indices):
        alpha = reconstruct_alpha(frames[index], source_alpha, preserve_source_core)
        image = composite(frames[index], alpha).resize((thumb_size, thumb_size), Image.Resampling.LANCZOS)
        column = slot % columns
        row = slot // columns
        x = column * thumb_size
        y = row * (thumb_size + label_height)
        sheet.paste(image, (x, y))
        draw.text((x + 8, y + thumb_size + 7), f"f{index:02d}", fill=(235, 235, 235), font=font)
    output = review_dir / "full-loop-contact-sheet.png"
    sheet.save(output)
    return output


def main() -> None:
    only = set(sys.argv[1:])
    results = []
    for video_path in sorted(RAW_DIR.glob("*.mp4")):
        stem = video_path.stem
        if only and stem not in only:
            continue
        source_path = SOURCE_DIR / f"{SOURCE_STEMS.get(stem, stem)}.png"
        if not source_path.exists():
            raise RuntimeError(f"Missing source PNG for {stem}")
        source_alpha = np.array(
            Image.open(source_path).convert("RGBA").getchannel("A").resize((SIZE, SIZE), Image.Resampling.LANCZOS)
        )
        forward_count, repeat = PROFILES.get(stem, (37, 1))
        start_frame = START_FRAMES.get(stem, 0)
        frame_scale = FRAME_SCALES.get(stem, 1.0)
        preserve_source_core = PRESERVE_SOURCE_CORE.get(stem, True)
        source_alpha = fit_with_margin(source_alpha, frame_scale)
        frames = read_loop_frames(video_path, start_frame, forward_count, repeat, frame_scale)
        output = encode_loop(stem, frames, source_alpha, preserve_source_core)
        review = render_review(stem, frames, source_alpha, preserve_source_core)
        results.append({"stem": stem, "output": str(output), "review": str(review), "frames": len(frames)})
        print(f"{stem}: encoded and rendered review", flush=True)
    manifest = Path(__file__).resolve().parent / "processed-manifest.json"
    manifest.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
