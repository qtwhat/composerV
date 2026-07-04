"""Developer utilities: generate synthetic CFR test clips.

The clips mimic composerV's proxy spec (uniform CFR, 720p, h264/yuv420p, 48k stereo AAC,
short GOP, faststart) so the preview engine can be exercised and validated without the
user's real footage.

For a *visual* frame-accuracy / seamlessness check we want each frame to be
self-identifying. The `testsrc` source already renders a running timestamp/counter. On
top of that we add a per-clip colored border (via `drawbox`) so it is obvious at a cut
which clip is on screen, and a distinct audio tone per clip so A/V sync and cut points
are audible. If the richer `drawtext` filter is available we also burn a big frame
number. The generator degrades gracefully to whatever filters this ffmpeg build has.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
]

# distinct, high-contrast border colors keyed by clip label
_BORDER_COLORS = ["red", "lime", "cyan", "yellow", "magenta", "orange", "white", "blue"]


@functools.lru_cache(maxsize=1)
def _available_filters() -> frozenset[str]:
    if shutil.which("ffmpeg") is None:
        return frozenset()
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True, check=False,
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # lines look like: " T.. name   in->out   desc"
        if len(parts) >= 2 and parts[0].isalpha() is False:
            names.add(parts[1])
    return frozenset(names)


def _font_file() -> str | None:
    return next((p for p in _FONT_CANDIDATES if os.path.exists(p)), None)


def _video_filters(label: str) -> str:
    filters: list[str] = []
    self_id: list[str] = []
    available = _available_filters()
    font = _font_file()
    if "drawtext" in available and font:
        esc = font.replace(":", r"\:")
        self_id.append(
            f"drawtext=fontfile='{esc}':text='%{{n}}':fontsize=140:fontcolor=white:"
            f"box=1:boxcolor=black@0.6:x=24:y=24"
        )
        self_id.append(
            f"drawtext=fontfile='{esc}':text='clip {label}':fontsize=72:fontcolor=yellow:"
            f"box=1:boxcolor=black@0.6:x=24:y=200"
        )
    if "drawbox" in available:
        color = _BORDER_COLORS[(ord(label[0]) - ord("A")) % len(_BORDER_COLORS)] if label else "white"
        filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color={color}:t=30")
    filters.extend(self_id)
    return ",".join(filters) if filters else "null"


def make_cfr_test_clip(
    path: str,
    seconds: float = 5.0,
    label: str = "A",
    tone_hz: int = 440,
    fps: int = 30,
    width: int = 1280,
    height: int = 720,
) -> str:
    """Render one uniform CFR test clip. Returns `path`. Raises if ffmpeg fails."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:duration={seconds}",
        "-vf", _video_filters(label),
        "-r", str(fps), "-fps_mode", "cfr",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(max(1, fps // 2)), "-profile:v", "high",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-loglevel", "error",
        path,
    ]
    subprocess.run(cmd, check=True)
    return path


def make_sign_clip(
    path: str,
    sign_text: str = "Riverside Park",
    seconds: float = 6.0,
    label: str = "B",
    tone_hz: int = 660,
    fps: int = 30,
    width: int = 1280,
    height: int = 720,
) -> str:
    """A test clip with a big centered "sign" so the VLM's OCR has something to read.

    Falls back to a plain test clip when this ffmpeg build lacks drawtext (or no font
    is found); the pipeline still runs, the OCR column just stays empty.
    """
    font = _font_file()
    if "drawtext" not in _available_filters() or not font:
        return make_cfr_test_clip(path, seconds, label, tone_hz, fps, width, height)
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    esc = font.replace(":", r"\:")
    sign = (
        f"drawtext=fontfile='{esc}':text='{sign_text}':fontsize=96:fontcolor=white:"
        f"box=1:boxcolor=black@0.75:boxborderw=28:x=(w-text_w)/2:y=(h-text_h)/2"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:duration={seconds}",
        "-vf", _video_filters(label) + "," + sign,
        "-r", str(fps), "-fps_mode", "cfr",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(max(1, fps // 2)), "-profile:v", "high",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-loglevel", "error",
        path,
    ]
    subprocess.run(cmd, check=True)
    return path


def make_speech_clip(
    path: str,
    text: str = "What a beautiful day at the park. Look at that view over the river!",
    label: str = "C",
    fps: int = 30,
    width: int = 1280,
    height: int = 720,
) -> str:
    """A test clip whose audio is synthesized speech (macOS `say`), so Whisper has
    something to transcribe and the director sees a real 'speech' row.

    Raises RuntimeError when `say` or ffmpeg is missing (caller decides whether to skip).
    """
    if shutil.which("say") is None:
        raise RuntimeError("say not found on PATH (macOS only)")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    aiff = path + ".say.aiff"
    try:
        subprocess.run(["say", "-o", aiff, text], check=True)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", aiff],
            capture_output=True, text=True, check=True,
        )
        seconds = float(probe.stdout.strip()) + 0.6  # a little air after the last word
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={seconds:.2f}",
            "-i", aiff,
            "-vf", _video_filters(label),
            "-af", f"apad=whole_dur={seconds:.2f}",
            "-r", str(fps), "-fps_mode", "cfr",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(max(1, fps // 2)), "-profile:v", "high",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            "-shortest",
            "-loglevel", "error",
            path,
        ]
        subprocess.run(cmd, check=True)
    finally:
        if os.path.exists(aiff):
            os.remove(aiff)
    return path


def make_demo_photo(path: str, width: int = 1280, height: int = 720) -> str:
    """A single still JPEG (the director treats photos as held beats)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=1:duration=1",
        "-frames:v", "1", "-loglevel", "error", path,
    ], check=True)
    return path


def make_demo_music(path: str, seconds: float = 60.0, bpm: float = 120.0, climax: float = 0.65) -> str:
    """Synthesize a music bed with a real beat grid and an energy arc, no downloads.

    Kick on every beat + hat on offbeats + a soft pad, all under a loudness envelope
    that rises to `climax` (fraction of the track) and falls off — enough structure for
    librosa beat tracking, TrackFeatures' energy_curve, and rank_tracks to be meaningful.
    Deterministic (seeded noise). Writes a 44.1k mono WAV.
    """
    import numpy as np
    import soundfile as sf

    sr = 44100
    n = int(seconds * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)

    kick_len = int(0.09 * sr)
    kt = np.arange(kick_len) / sr
    kick = np.sin(2 * np.pi * 110 * kt) * np.exp(-kt * 45)
    hat_len = int(0.03 * sr)
    hat = (np.random.default_rng(0).standard_normal(hat_len)
           * np.exp(-np.arange(hat_len) / sr * 180) * 0.25)

    beat = 60.0 / bpm
    b = 0.0
    while b < seconds:
        i = int(b * sr)
        seg = min(kick_len, n - i)
        if seg > 0:
            y[i:i + seg] += kick[:seg]
        j = int((b + beat / 2) * sr)
        seg = min(hat_len, n - j)
        if seg > 0:
            y[j:j + seg] += hat[:seg]
        b += beat

    # The pad must dominate per-frame RMS: TrackFeatures' 16-point energy_curve point-samples
    # the frame-level RMS curve, so drum transients louder than the pad read back as noise
    # instead of the macro arc.
    pad = 0.5 * (np.sin(2 * np.pi * 220.0 * t)
                 + np.sin(2 * np.pi * 277.18 * t)
                 + np.sin(2 * np.pi * 329.63 * t)) / 3.0  # A major triad
    env = np.interp(t / seconds, [0.0, climax, 1.0], [0.35, 1.0, 0.25])
    y = (0.5 * y + pad) * env
    y = 0.9 * y / max(1e-9, float(np.abs(y).max()))
    sf.write(path, y.astype("float32"), sr)
    return path


def make_demo_set(root: str, footage_seconds: float = 6.0, music_seconds: float = 60.0) -> dict:
    """Generate a self-contained demo set under `root`: footage/ + music/<feeling>/.

    Everything is synthetic (ffmpeg + macOS `say` + numpy): no downloads, no licenses,
    no personal media. The speech clip is skipped when `say` is unavailable.
    Returns {"footage": [paths], "music": [paths], "skipped": [notes]}.
    """
    footage_dir = os.path.join(root, "footage")
    os.makedirs(footage_dir, exist_ok=True)
    footage: list[str] = [
        make_cfr_test_clip(os.path.join(footage_dir, "motion.mp4"),
                           seconds=footage_seconds, label="A", tone_hz=440),
        make_sign_clip(os.path.join(footage_dir, "sign.mp4"),
                       seconds=footage_seconds, label="B"),
        make_demo_photo(os.path.join(footage_dir, "still.jpg")),
    ]
    skipped: list[str] = []
    if shutil.which("say"):
        footage.insert(2, make_speech_clip(os.path.join(footage_dir, "speech.mp4"), label="C"))
    else:
        skipped.append("speech.mp4 (no `say` on PATH; Whisper will have nothing to transcribe)")

    music: list[str] = []
    for feeling, bpm, climax in (("calm", 84.0, 0.5), ("upbeat", 128.0, 0.7)):
        d = os.path.join(root, "music", feeling)
        os.makedirs(d, exist_ok=True)
        music.append(make_demo_music(os.path.join(d, f"demo_{feeling}.wav"),
                                     seconds=music_seconds, bpm=bpm, climax=climax))
    return {"footage": footage, "music": music, "skipped": skipped}


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "test_clip.mp4"
    make_cfr_test_clip(out)
    print(f"wrote {out}")
