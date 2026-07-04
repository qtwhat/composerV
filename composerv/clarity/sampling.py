"""Content-aware frame selection: pick frames where the picture CHANGES, not uniformly.

A uniform 1fps sample wastes the model's budget on a near-static scene and can miss the
moment that matters. Here we detect scene/shot changes (ffmpeg's scene score), keep those
plus the opening frame, fill to a floor of coverage when a clip is calm, and cap to a
ceiling when it is busy. Fewer, better-placed frames = less prefill AND better understanding.

`choose_frame_times` is pure (the selection logic); detection and extraction shell ffmpeg.
"""

from __future__ import annotations

import os
import re
import subprocess


def choose_frame_times(
    scene_times: list[float],
    duration: float,
    max_frames: int = 12,
    min_frames: int = 4,
) -> list[float]:
    """Combine detected scene-change times with the opening frame; fill uniformly when too
    few, subsample evenly when too many. Returns sorted, de-duped times within [0, duration)."""
    if duration <= 0:
        return [0.0]
    target = max(min_frames, max_frames)  # fill toward the budget, not just the floor
    cands = sorted({0.0} | {round(t, 3) for t in scene_times if 0.0 <= t < duration})
    if len(cands) < target:
        fill = {round(duration * i / target, 3) for i in range(target)}
        cands = sorted(set(cands) | fill)
    if len(cands) > max_frames:
        step = len(cands) / max_frames
        cands = [cands[min(len(cands) - 1, int(i * step))] for i in range(max_frames)]
    return cands


def choose_by_cumulative_motion(profile: list[tuple[float, float]], n_frames: int) -> list[float]:
    """Place n frames by EQUAL CUMULATIVE MOTION: many frames where lots is changing, few
    where the picture is still. `profile` is [(t, motion_score)] sorted by t (score >= 0).

    Pure. Falls back to a uniform spread when there is no motion signal.
    """
    if not profile:
        return [0.0]
    times = [t for t, _s in profile]
    if n_frames <= 1:
        return [times[0]]
    scores = [max(0.0, s) for _t, s in profile]
    total = sum(scores)
    if total <= 0:  # no motion info -> uniform across the available times
        step = (len(times) - 1) / (n_frames - 1)
        return sorted({times[min(len(times) - 1, round(i * step))] for i in range(n_frames)})
    cum, run = [], 0.0
    for s in scores:
        run += s
        cum.append(run)
    out = []
    for i in range(n_frames):
        target = total * i / (n_frames - 1)
        idx = next((j for j, c in enumerate(cum) if c >= target), len(cum) - 1)
        out.append(times[idx])
    return sorted(set(out))


def motion_profile(
    video_path: str,
    base_fps: float = 2.0,
    sample_long_side: int = 64,
    frames_dir: str | None = None,
) -> list[tuple[float, float]]:
    """Sample frames at base_fps, return [(t, motion_score)] where score is the mean absolute
    pixel change from the previous sampled frame (on tiny grayscale frames -- cheap)."""
    import tempfile

    import numpy as np
    from PIL import Image

    from composerv.index.frames import sample_frames

    frames_dir = frames_dir or tempfile.mkdtemp(prefix="cv_motion_")
    fr = sample_frames(video_path, frames_dir, fps=base_fps)
    arrs = []
    for f in fr:
        im = Image.open(f.image_path).convert("L")
        im.thumbnail((sample_long_side, sample_long_side))
        arrs.append(np.asarray(im, dtype=np.float32))
    profile: list[tuple[float, float]] = []
    for i, f in enumerate(fr):
        if i == 0:
            score = 0.0
        else:
            a, b = arrs[i], arrs[i - 1]
            h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
            score = float(np.mean(np.abs(a[:h, :w] - b[:h, :w])))
        profile.append((f.src_pts_s, score))
    return profile


def detect_scene_changes(video_path: str, threshold: float = 0.3) -> list[float]:
    """Timestamps where ffmpeg's scene score exceeds `threshold` (a shot/content change)."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return []
    times = []
    for line in proc.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def select_frames(video_path: str, out_dir: str, times: list[float]) -> list[tuple[float, str]]:
    """Extract one frame at each timestamp; return (t, path), keeping only frames written."""
    os.makedirs(out_dir, exist_ok=True)
    out: list[tuple[float, str]] = []
    for i, t in enumerate(times):
        path = os.path.join(out_dir, f"kf_{i:03d}.jpg")
        cmd = ["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", video_path,
               "-frames:v", "1", "-q:v", "3", "-loglevel", "error", path]
        try:
            subprocess.run(cmd, check=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue
        if os.path.exists(path):
            out.append((t, path))
    return out


def downscale_frames(paths: list[str], out_dir: str, max_long_side: int) -> list[str]:
    """Resize each frame so its longest side <= max_long_side (aspect preserved); copy through
    anything already small enough. Fewer pixels => fewer vision tokens => faster prefill."""
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    out: list[str] = []
    for i, p in enumerate(paths):
        im = Image.open(p)
        w, h = im.size
        longest = max(w, h)
        if longest > max_long_side:
            s = max_long_side / longest
            im = im.resize((max(1, round(w * s)), max(1, round(h * s))))
        q = os.path.join(out_dir, f"ds_{i:03d}.jpg")
        im.convert("RGB").save(q, quality=85)
        out.append(q)
    return out


def select_keyframes(
    video_path: str,
    out_dir: str,
    duration_s: float,
    max_frames: int = 12,
    min_frames: int = 4,
    threshold: float = 0.3,
    mode: str = "scene",
) -> list[tuple[float, str]]:
    """Pick frames content-aware and extract them.

    mode='motion'  -> place frames by within-shot motion (best for single-shot footage).
    mode='scene'   -> anchor on hard cuts (best for multi-shot edits), uniform-filled.
    mode='uniform' -> evenly spaced.
    """
    if mode == "motion":
        times = choose_by_cumulative_motion(motion_profile(video_path), max_frames)
    else:
        scene = detect_scene_changes(video_path, threshold) if mode == "scene" else []
        times = choose_frame_times(scene, duration_s, max_frames=max_frames, min_frames=min_frames)
    return select_frames(video_path, out_dir, times)
