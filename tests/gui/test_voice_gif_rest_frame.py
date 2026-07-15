from pathlib import Path

from PIL import Image


VOICE_GIF = Path(__file__).resolve().parents[2] / "assets" / "img" / "voice.gif"


def _active_waveform_height(frame: Image.Image) -> int:
    rgba = frame.convert("RGBA")
    active_y = [
        y
        for y in range(rgba.height)
        for x in range(rgba.width)
        if rgba.getpixel((x, y))[3] and max(rgba.getpixel((x, y))[:3]) > 35
    ]
    return max(active_y) - min(active_y) + 1 if active_y else 0


def test_voice_gif_begins_on_flat_resting_waveform():
    movie = Image.open(VOICE_GIF)
    assert movie.n_frames == 348
    assert movie.info.get("loop") == 0

    movie.seek(0)
    resting_height = _active_waveform_height(movie.copy())
    movie.seek(144)  # Far end of the first, reversed half-cycle.
    pulse_height = _active_waveform_height(movie.copy())

    assert resting_height <= 8
    assert pulse_height >= resting_height * 4


def test_voice_gif_has_uniform_frame_timing():
    movie = Image.open(VOICE_GIF)
    durations = []
    for frame_index in range(movie.n_frames):
        movie.seek(frame_index)
        durations.append(movie.info.get("duration"))
    assert set(durations) == {30}
